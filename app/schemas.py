from pydantic import BaseModel
from pydantic import BaseModel, EmailStr

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    company_id: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str