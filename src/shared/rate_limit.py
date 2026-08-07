from slowapi import Limiter
from slowapi.util import get_remote_address

# In-memory storage: fine for a single instance, and the limit is a brute-force
# speed bump rather than a correctness guarantee. Running more than one replica
# means pointing this at Redis via `storage_uri`, otherwise each replica
# enforces the limit independently.
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
