from typing import Dict, Optional, Any, List
from google.adk.sessions.session import Session
from google.adk.sessions.base_session_service import BaseSessionService, ListSessionsResponse
import pymongo
import datetime
import uuid
import logging

logger = logging.getLogger(__name__)

class MongoDBSessionService(BaseSessionService):
    def __init__(self, db_url: str):
        super().__init__()
        self._client = pymongo.MongoClient(db_url)
        # Using a fixed database name 'firestore-mongo' for compatibility or derived from URI if possible
        # defaulting to 'personal_assistant_db' if not specified, but user mentioned Firestore with MongoDB compatibility
        # which often implies a specific structure. Stash used 'firestore-mongo' in the try/except block.
        try:
            self._db = self._client.get_database()
        except:
            self._db = self._client["firestore-mongo"]
            
        self._sessions_col = self._db["sessions"]

    async def create_session(self, *, app_name: str, user_id: str, session_id: Optional[str] = None, **kwargs) -> Session:
        sid = session_id or str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        session_data = {
            "session_id": sid,
            "app_name": app_name,
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
            "state": {}
        }
        # Pymongo is sync, wrapping or using directly (ADK often runs sync in async wrappers if needed, but here we just call it)
        # For production async mongo (motor) is better, but following Stash pattern.
        self._sessions_col.update_one(
            {"session_id": sid},
            {"$set": session_data},
            upsert=True
        )
        return Session(
            id=sid,
            app_name=app_name,
            user_id=user_id,
            state={},
            last_update_time=now.timestamp()
        )

    async def get_session(self, *, app_name: str, user_id: str, session_id: str, **kwargs) -> Optional[Session]:
        doc = self._sessions_col.find_one({"session_id": session_id})
        if not doc:
            return None
        
        return Session(
            id=doc["session_id"],
            app_name=doc.get("app_name", app_name),
            user_id=doc.get("user_id", user_id),
            state=doc.get("state", {}),
            last_update_time=doc.get("updated_at", datetime.datetime.utcnow()).timestamp() if isinstance(doc.get("updated_at"), datetime.datetime) else datetime.datetime.utcnow().timestamp()
        )

    async def update_session(self, session: Session) -> Session:
        now = datetime.datetime.utcnow()
        session.last_update_time = now.timestamp()
        self._sessions_col.update_one(
            {"session_id": session.id},
            {"$set": {
                "state": session.state,
                "updated_at": now,
                "user_id": session.user_id 
            }}
        )
        return session
    
    async def delete_session(self, *, app_name: str, user_id: str, session_id: str, **kwargs) -> None:
        self._sessions_col.delete_one({"session_id": session_id})

    async def list_sessions(self, *, app_name: str, user_id: Optional[str] = None, **kwargs) -> ListSessionsResponse:
        filter_doc = {}
        if app_name:
            filter_doc["app_name"] = app_name
        if user_id:
            filter_doc["user_id"] = user_id
            
        docs = self._sessions_col.find(filter_doc)
        results = []
        for doc in docs:
             results.append(Session(
                id=doc["session_id"],
                app_name=doc.get("app_name", ""),
                user_id=doc.get("user_id", ""),
                state=doc.get("state", {}),
                last_update_time=doc.get("updated_at", datetime.datetime.utcnow()).timestamp() if isinstance(doc.get("updated_at"), datetime.datetime) else datetime.datetime.utcnow().timestamp()
            ))
        return ListSessionsResponse(sessions=results)
