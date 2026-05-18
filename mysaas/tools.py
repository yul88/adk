from typing import Optional, List, Dict, Any
from google.adk.tools.tool_context import ToolContext
import pymongo
import datetime
import logging
import re

logger = logging.getLogger(__name__)

# Database connection reuse logic should be in server.py, 
# but for tools to work independently they need access to the db.
# We will inject the db collection into the tool or the agent.
# For simplicity, we can set up a global client here or (better) 
# have the tool implementation class that holds the db reference.

class ChecklistTools:
    def __init__(self, db_client):
        self._db = db_client["firestore-mongo"]
        self._checklists_col = self._db["checklists"]

    def save_checklist(self, title: str, content: str, tool_context: Optional[ToolContext] = None) -> str:
        """
        Saves a checklist to the user's account.
        
        Args:
            title: The title of the checklist.
            content: The markdown content of the checklist.
        """
        user = self._get_user(tool_context)
        if not user:
            return "Please sign in to save checklists."

        # Parse markdown content into structured items
        items = self._parse_markdown_checklist(content)
        
        checklist_doc = {
            "username": user,
            "title": title,
            "items": items,
            "original_content": content,
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow()
        }
        
        try:
            self._checklists_col.insert_one(checklist_doc)
            return f"Checklist '{title}' saved successfully with {len(items)} items."
        except Exception as e:
            logger.error(f"Error saving checklist: {e}")
            return f"Error saving checklist: {str(e)}"

    def _get_user(self, tool_context: Optional[ToolContext] = None) -> Optional[str]:
        if tool_context and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
            inv_ctx = tool_context._invocation_context
            if inv_ctx.session and inv_ctx.session.state:
                return inv_ctx.session.state.get("current_user")
        return None

    def _parse_markdown_checklist(self, content: str) -> List[Dict[str, Any]]:
        """
        Parses markdown checklist items into structured objects, supporting up to 3 levels of nesting.
        """
        items = []
        lines = content.split('\n')
        
        for line in lines:
            if not line.strip():
                continue
            
            # Calculate indentation level (2 spaces per level typically)
            indent = len(line) - len(line.lstrip())
            level = 0
            if indent >= 6:
                level = 3
            elif indent >= 4:
                level = 2
            elif indent >= 2:
                level = 1
            
            stripped_line = line.strip()
            
            # Check for checked/unchecked pattern
            checked_match = re.match(r'^[-*]\s+\[([xX ])\]\s+(.*)', stripped_line)
            
            if checked_match:
                is_checked = checked_match.group(1).lower() == 'x'
                text = checked_match.group(2).strip()
                items.append({
                    "text": text,
                    "is_checked": is_checked,
                    "level": level
                })
            elif stripped_line.startswith('- ') or stripped_line.startswith('* '):
                # Bullet point without checkbox, treat as unchecked item (group header?)
                text = stripped_line[2:].strip()
                items.append({
                    "text": text,
                    "is_checked": False,
                    "level": level
                })
                
        return items
