import time


def retry(attempts=3, delay=1.0, exceptions=(Exception,), sleep=time.sleep):
    """Decorator: call the function up to `attempts` times.

    - Retry only when one of `exceptions` is raised; other exceptions propagate
      immediately.
    - Wait `delay` seconds before the 2nd try, then double it each time
      (delay, 2*delay, 4*delay ...), using `sleep` so tests can fake it.
    - If every attempt fails, re-raise the last exception.
    - The wrapped function keeps its __name__ and __doc__.
    """
    raise NotImplementedError
