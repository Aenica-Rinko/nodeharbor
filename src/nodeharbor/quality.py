"""Repeat bounded HTTP and HTTPS checks without implying a speed benchmark."""
from .storage import AppError
from .tasks import Cancelled


def assess(probe, options, control):
    urls = [options['test_url'], options.get('https_test_url', 'https://www.gstatic.com/generate_204')]
    samples, attempts, successes = [], 0, 0
    rounds = options.get('rounds', 2)
    for _ in range(rounds):
        for _retry in range(options.get('retry_failed', 1) + 1):
            delays = []
            for url in urls:
                control.check(); attempts += 1
                try: delay = probe(url)
                except Cancelled: raise
                except AppError: control.check(); delay = None
                if type(delay) not in (int, float) or not 0 < delay <= options['max_delay_ms']: break
                delays.append(delay)
            if len(delays) == len(urls):
                samples.append(max(delays)); successes += 1
                break
    return {'quality': 'stable' if successes == rounds else 'basic' if successes else 'failed',
            'successful_rounds': successes, 'rounds': rounds, 'requests': attempts,
            'delay_ms': round(sum(samples) / len(samples)) if samples else None,
            'samples_ms': samples}


