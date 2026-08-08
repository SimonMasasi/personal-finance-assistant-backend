from pydantic import BaseModel, ConfigDict, Field

# Bare snake_case, no alias generator. The reference project camelCases its wire
# format; this one does not, because the sync payload's field names must match
# the client's SQLite column names exactly and auth should not disagree with it.


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str


class GoogleSignInRequest(BaseModel):
    """The ID token the device obtained from Google.

    `extra="forbid"` because there is exactly one legitimate field here and a
    request carrying anything else — an email, a display name, a user id — is
    either a client bug or someone testing what this endpoint will believe.
    Everything about the user comes out of the verified token, never the body.
    """

    model_config = ConfigDict(extra="forbid")

    # A Google ID token is a JWT of roughly 1 KB; the ceiling just stops an
    # unbounded string reaching the crypto layer.
    id_token: str = Field(min_length=1, max_length=4096)


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
