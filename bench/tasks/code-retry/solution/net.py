import functools
import time


def retry(attempts=3, delay=1.0, exceptions=(Exception,), sleep=time.sleep):
    """Decorator: call the function up to `attempts` times (see tests)."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            wait = delay
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if i == attempts - 1:
                        raise
                    sleep(wait)
                    wait *= 2
        return inner
    return wrap
