"""监控管理（在线用户 / 服务器 / 缓存）响应模型。"""

from pydantic import BaseModel, Field


class OnlineSessionRow(BaseModel):
    sid: str
    uid: int
    account: str
    nickname: str | None = None
    role: str
    device: str
    ip: str
    created_at: int
    last_seen: int


class OnlineSessionsData(BaseModel):
    available: bool = True
    items: list[OnlineSessionRow] = Field(default_factory=list)
    total: int = 0
    unique_users: int = 0
    page: int = 1
    page_size: int = 20
    scanned: int = 0


class ServerStatsData(BaseModel):
    available: bool = True
    error: str | None = None
    platform: str = ""
    python_version: str = ""
    cpu_count: int = 0
    cpu_percent: float = 0
    load_avg: list[float] | None = None
    memory_total: int = 0
    memory_used: int = 0
    memory_percent: float = 0
    swap_total: int = 0
    swap_used: int = 0
    swap_percent: float = 0
    disk_total: int = 0
    disk_used: int = 0
    disk_percent: float = 0
    boot_time: int = 0
    uptime_seconds: int = 0
    process_pid: int = 0
    process_memory: int = 0
    process_threads: int = 0


class CachePrefixRow(BaseModel):
    prefix: str
    count: int


class CacheStatsData(BaseModel):
    available: bool = True
    error: str | None = None
    version: str = ""
    used_memory: int = 0
    used_memory_human: str = ""
    peak_memory_human: str = ""
    maxmemory_human: str = ""
    connected_clients: int = 0
    total_keys: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0
    uptime_seconds: int = 0
    scanned: int = 0
    truncated: bool = False
    prefixes: list[CachePrefixRow] = Field(default_factory=list)
