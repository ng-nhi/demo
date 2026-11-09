"""Trạng thái dùng chung giữa các request: độ sâu hàng đợi (để trả 429), cache câu trả lời, thống kê.

Khớp với gateway benchmark của Người 2 (cùng tên khóa Redis: queue:depth, cache:<hash>, stat:hit/miss/rejected)
nên script phân tích của họ đọc được số liệu của app thật.

- Đặt REDIS_URL (ví dụ redis://127.0.0.1:6379/0): dùng Redis, nhiều worker uvicorn dùng chung được.
- Không đặt, hoặc Redis lỗi giữa chừng: tự chuyển sang bộ nhớ trong tiến trình (chỉ đúng với 1 worker).
"""
import hashlib
import logging
import time

from .menu import norm

log = logging.getLogger("uvicorn.error")

DEPTH_KEY = "queue:depth"


class Shared:
    def __init__(self, redis_url=None, max_mem_cache=2000, depth_key=DEPTH_KEY):
        self._dkey = depth_key
        self._url = redis_url or None
        self._r = None
        self._failed = False
        self._mem_depth = 0
        self._mem_cache = {}   # key -> (hết hạn, giá trị)
        self._mem_stats = {}
        self._max = max_mem_cache

    # ---------- kết nối ----------
    @property
    def backend(self):
        return "redis" if (self._url and not self._failed) else "memory"

    def _redis(self):
        if not self._url or self._failed:
            return None
        if self._r is None:
            try:
                import redis.asyncio as aioredis
                self._r = aioredis.from_url(self._url, decode_responses=True)
            except Exception as e:  # chưa cài thư viện redis, URL sai...
                self._fail(e)
                return None
        return self._r

    def _fail(self, e):
        if not self._failed:
            log.warning("Redis lỗi (%s: %s) -> chuyển sang bộ nhớ trong tiến trình (chỉ đúng với 1 worker).", type(e).__name__, e)
        self._failed = True
        self._r = None

    # ---------- hàng đợi (429) ----------
    async def depth_incr(self):
        r = self._redis()
        if r is not None:
            try:
                return int(await r.incr(self._dkey))
            except Exception as e:
                self._fail(e)
        self._mem_depth += 1
        return self._mem_depth

    async def depth_decr(self):
        r = self._redis()
        if r is not None:
            try:
                if int(await r.decr(self._dkey)) < 0:
                    await r.set(self._dkey, 0)
                return
            except Exception as e:
                self._fail(e)
        self._mem_depth = max(0, self._mem_depth - 1)

    async def depth(self):
        r = self._redis()
        if r is not None:
            try:
                return int(await r.get(self._dkey) or 0)
            except Exception as e:
                self._fail(e)
        return self._mem_depth

    async def reset(self):
        """Gọi lúc khởi động: xóa bộ đếm cũ (tiến trình trước bị tắt đột ngột có thể để lại số dư)."""
        r = self._redis()
        if r is not None:
            try:
                await r.set(self._dkey, 0)
            except Exception as e:
                self._fail(e)
        self._mem_depth = 0

    # ---------- cache ----------
    @staticmethod
    def cache_key(version, text):
        """version = dấu vân tay của prompt: đổi menu/prompt thì cache cũ tự vô hiệu."""
        return "cache:" + hashlib.sha256(f"{version}|{norm(text)}".encode("utf-8")).hexdigest()

    async def cache_get(self, key):
        r = self._redis()
        if r is not None:
            try:
                return await r.get(key)
            except Exception as e:
                self._fail(e)
        hit = self._mem_cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        self._mem_cache.pop(key, None)
        return None

    async def cache_set(self, key, value, ttl):
        r = self._redis()
        if r is not None:
            try:
                await r.set(key, value, ex=ttl)
                return
            except Exception as e:
                self._fail(e)
        if len(self._mem_cache) >= self._max:
            self._mem_cache.pop(next(iter(self._mem_cache)))  # bỏ mục cũ nhất
        self._mem_cache[key] = (time.monotonic() + ttl, value)

    # ---------- thống kê ----------
    async def stat_incr(self, name):
        r = self._redis()
        if r is not None:
            try:
                await r.incr(f"stat:{name}")
                return
            except Exception as e:
                self._fail(e)
        self._mem_stats[name] = self._mem_stats.get(name, 0) + 1

    async def stats(self):
        out = {}
        for name in ("hit", "miss", "rejected"):
            v = None
            r = self._redis()
            if r is not None:
                try:
                    v = int(await r.get(f"stat:{name}") or 0)
                except Exception as e:
                    self._fail(e)
            out[name] = v if v is not None else self._mem_stats.get(name, 0)
        total = out["hit"] + out["miss"]
        out["cache_hit_pct"] = round(100 * out["hit"] / total, 1) if total else 0.0
        out["queue_depth"] = await self.depth()
        out["backend"] = self.backend
        return out
