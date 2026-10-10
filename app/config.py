from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://agraharam:change-me@localhost:5432/agraharam'
    app_env: str = 'development'
    allowed_origins: str = 'http://localhost:5181,http://127.0.0.1:5181'
    cookie_secure: bool = False
    session_hours: int = 12
    offline_signing_key: str = ''
    passcode_encryption_key: str = ''
    frontend_dir: str = ''
    @property
    def origins(self):
        return [x.strip().rstrip('/') for x in self.allowed_origins.split(',') if x.strip()]

config = Config()
if config.app_env != 'test' and not config.database_url.startswith('postgresql+psycopg://'):
    raise RuntimeError('This application requires a PostgreSQL psycopg database URL.')
if config.app_env == 'production' and not config.cookie_secure:
    raise RuntimeError('COOKIE_SECURE=true is required in production.')
