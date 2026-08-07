from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Bare snake_case, no alias generator. The reference project camelCases its wire
# format; this one does not, because the sync payload's field names must match
# the client's SQLite column names exactly and auth should not disagree with it.


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshRequest(BaseModel):
    refresh: str = Field(min_length=1, max_length=512)


class TokenPairResponse(BaseModel):
    access: str
    refresh: str


class AuthResponse(BaseModel):
    access: str
    refresh: str
    user: UserDTO


class DeviceRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
