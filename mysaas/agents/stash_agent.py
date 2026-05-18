from google.adk.agents.llm_agent import Agent
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field, PrivateAttr
from typing import List, Optional, Any, Dict
import pymongo
from .stash_tools import scrape_url
import datetime
import logging
import hashlib
import os

logger = logging.getLogger(__name__)

# --- Data Models ---

class LinkAnalysis(BaseModel):
    summary: str = Field(description="A concise summary of the web page content.")
    tags: List[str] = Field(description="A list of relevant tags for categorizing the content.")

# --- Sub-Agent ---

class LinkProcessor(Agent):
    def __init__(self):
        super().__init__(
            model="gemini-2.5-flash",
            name="link_processor",
            instruction=(
                "You are an intelligent assistant that analyzes web page content. "
                "Your goal is to provide a concise summary and relevant tags for the provided text. "
                "You must ignore navigation menus, ads, and other noise, focusing only on the main content. "
                "You MUST return the result in valid JSON format with the following keys: "
                "'summary' (string) and 'tags' (list of strings). "
                "Do NOT include any markdown formatting like ```json ... ```."
            )
        )

# --- Main Agent ---

class Stash(Agent):
    _user_sessions: Dict[str, str] = PrivateAttr(default_factory=dict)
    _client: Any = PrivateAttr()
    _db: Any = PrivateAttr()
    _users_col: Any = PrivateAttr()
    _links_col: Any = PrivateAttr()
    _link_processor: LinkProcessor = PrivateAttr()
    _session_service: Any = PrivateAttr()

    def __init__(self):
        super().__init__(
            model="gemini-2.5-flash",
            name="stash",
            instruction=(
                "You are Stash, an intelligent agent that helps users save and organize web links. "
                "You allow users to signup, signin, and stash links. "
                "PROCESS FLOW for stashing A LINK: "
                "1. User says 'Stash <url>'. "
                "2. Call `stash_link_smart(url)`. "
                "3. This tool will handle scraping, summarizing, and saving automatically. "
                "4. Output the result returned by the tool. "
                "5. Output the result returned by the tool. "
                "CRITICAL AUTHENTICATION RULE: "
                "The user's login state is managed externally and transparency. "
                "Even if the conversation history shows 'signout', the user might have logged in via a different tab or mechanism. "
                "ALWAYS call the requested tool (e.g., `get_links`, `stash_link_smart`) FIRST to check the actual state. "
                "ONLY ask the user to sign in if the TOOL returns an error starting with 'Please sign in'. "
                "Never guess the auth state from chat history."
            ),
            tools=[self.signup, self.signin, self.signout, self.stash_link_smart, self.get_links, self.search_links],
        )
        
        # Initialize sub-agent
        self._link_processor = LinkProcessor()
        self._user_sessions = {}
        
        from google import genai
        from dotenv import load_dotenv
        from pathlib import Path
        
        # Load .env from parent directory (adk/mySaaS/.env) since this is in adk/mySaaS/agents/
        env_path = Path(__file__).parent.parent / '.env'
        load_dotenv(dotenv_path=env_path)
        
        # Load config from environment
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "deckardy-ce-001-370404")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        mongo_uri = os.environ.get("MONGO_URI")
        
        if not mongo_uri:
             # Fallback or error
             raise ValueError("MONGO_URI environment variable is not set")
 
        self._genai_client = genai.Client(vertexai=True, project=project, location=location)
        # Persistence setup (Application Data)
        self._client = pymongo.MongoClient(mongo_uri)
        self._db = self._client["firestore-mongo"]
        self._users_col = self._db["users"]
        self._links_col = self._db["links"]
        
        # Session Service Setup (Custom Mongo/Firestore)
        from ..mongo_session_service import MongoDBSessionService
        self._session_service = MongoDBSessionService(db_url=mongo_uri)

    def query(self, input: str) -> str:
        """
        Required for Reasoning Engine compatibility.
        Routes a message to the agent's internal logic.
        """
        return self.run_live(input).answer

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def _get_session_id(self, tool_context: Optional[ToolContext]) -> Optional[str]:
        if tool_context and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
            inv_ctx = tool_context._invocation_context
            if inv_ctx.session:
                return inv_ctx.session.id
        return None

    async def _get_user(self, tool_context: Optional[ToolContext] = None) -> Optional[str]:
        # 1. Try ToolContext session state (Best practice if infrastructure supports it)
        if tool_context and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
            inv_ctx = tool_context._invocation_context
            if inv_ctx.session and inv_ctx.session.state:
                req_user = inv_ctx.session.state.get("current_user")
                if req_user:
                    return req_user

        session_id = self._get_session_id(tool_context)
        if not session_id:
            return None

        # 2. Try internal in-memory session store (Fast cache)
        if session_id in self._user_sessions:
            return self._user_sessions[session_id]

        # 3. Try DatabaseSessionService
        try:
             # get_session returns Optional[Session]
             session = await self._session_service.get_session(
                 app_name="stash",
                 user_id="unknown", # We don't know the user yet
                 session_id=session_id
             )
             if session and session.state:
                 user = session.state.get("current_user")
                 if user:
                     self._user_sessions[session_id] = user
                     return user
        except Exception as e:
             logger.error(f"Error reading session from DB service: {e}")

        return None

    async def _set_user(self, username: Optional[str], tool_context: Optional[ToolContext] = None):
        # 1. Update ToolContext session state (Ephemeral for this turn)
        if tool_context and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
            tool_context._invocation_context.session.state["current_user"] = username
        
        session_id = self._get_session_id(tool_context)
        if session_id:
            # 2. Update internal cache
            if username:
                self._user_sessions[session_id] = username
            else:
                self._user_sessions.pop(session_id, None)
            
            # 3. Update DatabaseSessionService (Persistent)
            try:
                # Need valid session object to update
                session = await self._session_service.get_session(
                    app_name="stash",
                    user_id=username or "unknown",
                    session_id=session_id
                )
                if not session:
                    session = await self._session_service.create_session(
                        app_name="stash",
                        user_id=username or "unknown",
                        session_id=session_id 
                    )
                
                # Update state
                if session.state is None:
                    session.state = {}
                session.state["current_user"] = username
                
                await self._session_service.update_session(session)
            except Exception as e:
                logger.error(f"Error writing session to DB service: {e}")

    async def signup(self, username: str, password: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Registers a new user with a password.
        """
        if self._users_col.find_one({"username": username}):
            return f"User '{username}' already exists."
        
        hashed_pw = self._hash_password(password)
        self._users_col.insert_one({
            "username": username, 
            "password": hashed_pw, 
            "created_at": datetime.datetime.utcnow()
        })
        await self._set_user(username, tool_context)
        return f"User '{username}' signed up and logged in successfully."

    async def signin(self, username: str, password: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Logs in an existing user with their password.
        """
        user_doc = self._users_col.find_one({"username": username})
        if not user_doc:
            return f"User '{username}' does not exist. Please sign up first."
        
        if user_doc.get("password") != self._hash_password(password):
            return "Incorrect password."
        
        await self._set_user(username, tool_context)
        return f"User '{username}' signed in successfully."

    async def signout(self, tool_context: Optional[ToolContext] = None) -> str:
        """
        Logs out the current user.
        """
        user = await self._get_user(tool_context)
        if not user:
            return "No user is currently signed in."
        
        await self._set_user(None, tool_context)
        return f"User '{user}' signed out."

    async def stash_link_smart(self, url: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Smartly stashes a link by scraping, analyzing, and saving it. 
        Use this single tool when the user wants to stash/save a link.
        """
        user = await self._get_user(tool_context)
        if not user:
            return "Please sign in to stash links."
            
        # 1. Scrape
        content = self.scrape_page(url)
        if content.startswith("Failed"):
            return content
            
        # 2. Analyze
        try:
            prompt = (
                f"Analyze the following web page content. Return a valid JSON object with keys "
                f"'summary' (string) and 'tags' (list of strings). Content:\n\n{content[:10000]}"
            )
            response = self._genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            
            import json
            analysis = json.loads(response.text)
            summary = analysis.get("summary", "No summary available.")
            tags = analysis.get("tags", [])
            
            # 3. Save
            return await self.save_link(url, summary, tags, tool_context)
            
        except Exception as e:
            logger.error(f"Error in stash_link_smart: {e}")
            return await self.save_link(url, "Analysis failed, saved raw link.", ["stashed"], tool_context)

    def scrape_page(self, url: str) -> str:
        """
        Fetches the content of a web page.
        """
        content = scrape_url(url)
        if content.startswith("Error"):
            return f"Failed to scrape URL: {content}"
        return content

    async def save_link(self, url: str, summary: str, tags: List[str], tool_context: Optional[ToolContext] = None) -> str:
        """
        Saves a web link with its summary and tags.
        """
        user = await self._get_user(tool_context)
        if not user:
            return "Please sign in to stash links."

        link_doc = {
            "username": user,
            "url": url,
            "summary": summary,
            "tags": tags,
            "created_at": datetime.datetime.utcnow()
        }
        self._links_col.insert_one(link_doc)

        return f"Link stashed! \nSummary: {summary}\nTags: {tags}"

    async def get_links(self, tool_context: Optional[ToolContext] = None) -> str:
        """
        Retrieves all stashed links for the current user.
        """
        user = await self._get_user(tool_context)
        if not user:
            return "Please sign in to view your links."
            
        links = list(self._links_col.find({"username": user}))
        if not links:
            return "No links found."
            
        result = "| Tag | Summary | Link |\n| --- | ------- | ---- |\n"
        for link in links:
            tags = ", ".join(link['tags'])
            summary = link['summary'].replace("|", "\\|") # Escape pipes
            url = link['url']
            result += f"| {tags} | {summary} | [{url}]({url}) |\n"
            
        return result

    async def search_links(self, query: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Searches stashed links by keyword (in URL/Summary) or tag.
        """
        user = await self._get_user(tool_context)
        if not user:
            return "Please sign in to search links."

        regex_query = {"$regex": query, "$options": "i"}
        
        filter_doc = {
            "username": user,
            "$or": [
                {"url": regex_query},
                {"summary": regex_query},
                {"tags": regex_query}
            ]
        }
        
        links = list(self._links_col.find(filter_doc))
        if not links:
            return f"No links found matching '{query}'."
            
        result = f"Found {len(links)} links matching '{query}':\n\n"
        result += "| Tag | Summary | Link |\n| --- | ------- | ---- |\n"
        for link in links:
            tags = ", ".join(link['tags'])
            summary = link['summary'].replace("|", "\\|") # Escape pipes
            url = link['url']
            result += f"| {tags} | {summary} | [{url}]({url}) |\n"
        return result

# Instantiate the agent for ADK CLI (if needed, though server.py instantiates its own)
root_agent = Stash()
