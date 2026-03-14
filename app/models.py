from typing import List, Optional, Any
from pydantic import BaseModel

class AppId(BaseModel):
    id: str

class WebhookInfo(BaseModel):
    id: str
    version: str

class SwitchboardIntegration(BaseModel):
    id: str
    name: str
    integrationId: str
    integrationType: str

class ConversationInfo(BaseModel):
    id: str
    type: str
    brandId: str
    activeSwitchboardIntegration: Optional[SwitchboardIntegration] = None

class AuthenticatedUser(BaseModel):
    id: str
    authenticated: bool

class Author(BaseModel):
    userId: str
    displayName: Optional[str] = None
    type: str
    user: Optional[AuthenticatedUser] = None

class MessageContent(BaseModel):
    type: str
    text: Optional[str] = None
    
class RawClientAttr(BaseModel):
    userId: Optional[str] = None
    displayName: Optional[str] = None
    language: Optional[str] = None

class SourceClient(BaseModel):
    integrationId: str
    type: str
    externalId: str
    id: str
    displayName: Optional[str] = None
    status: Optional[str] = None
    raw: Optional[RawClientAttr | dict[str, Any]] = None
    lastSeen: Optional[str] = None
    linkedAt: Optional[str] = None
    avatarUrl: Optional[str] = None

class MessageSource(BaseModel):
    type: str
    integrationId: str
    originalMessageId: Optional[str] = None
    client: Optional[SourceClient] = None

class MessagePayload(BaseModel):
    id: str
    received: str
    author: Author
    content: MessageContent
    source: MessageSource

class WebhookEventPayload(BaseModel):
    conversation: Optional[ConversationInfo] = None
    message: Optional[MessagePayload] = None

class WebhookEvent(BaseModel):
    id: str
    createdAt: str
    type: str
    payload: WebhookEventPayload

class WebhookPayload(BaseModel):
    app: AppId
    webhook: WebhookInfo
    events: List[WebhookEvent]
