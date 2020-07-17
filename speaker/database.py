import redis

_db = None
_DB_ID = 0


def get_database() -> 'Database':
    '''Return a Redis instance and ensure device "ident" is present.'''
    global _db
    if _db is None:
        _db = Database(_DB_ID)
        with _db.lock('ident-lock'):
            ident = _db.get_str('ident')
            if not ident:
                import uuid
                _db.set('ident', str(uuid.uuid4()))
    return _db


class Database(redis.Redis):
    '''Thin wrapper around Redis to provide string and int getters.'''

    def __init__(self, db_id: int) -> None:
        super().__init__(db=db_id)

    def get_int(self, key: str) -> int:
        '''Get value as an int.'''
        value = self.get(key)
        try:
            return int(value)
        except TypeError:
            return 0

    def get_str(self, key: str) -> str:
        '''Get value as a string, assuming utf-8 encoding.'''
        value = self.get(key)
        return value.decode('utf-8') if value else ''
