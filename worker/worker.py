import logging
import os
import time

from redis import Redis
from redis.exceptions import RedisError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    client = Redis.from_url(redis_url, decode_responses=True)
    logging.info("worker started")

    while True:
        try:
            client.set("agent_loop:worker:heartbeat", str(int(time.time())), ex=30)
            logging.info("heartbeat written")
        except RedisError as exc:
            logging.warning("redis unavailable: %s", exc)
        time.sleep(10)


if __name__ == "__main__":
    main()

