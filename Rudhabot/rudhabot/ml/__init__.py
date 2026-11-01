"""Clairvoyance: Rudhabot's in-house machine learning (see docs/ML.md).

Every matrix here is small (a few hundred units) and inference runs in tiny batches, so a multi-threaded BLAS only
adds overhead: with OpenBLAS's default thread pool, four self-play workers ran ~9x slower than single-threaded ones.
`single_threaded_blas()` pins the math libraries to one thread; it must run before numpy is first imported, which is
why main.py and every ML command-line entry point call it first. Values already set in the environment win.
"""
import os

_BLAS_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def single_threaded_blas() -> None:
    for name in _BLAS_THREAD_VARS:
        os.environ.setdefault(name, "1")


single_threaded_blas()          # importing any rudhabot.ml module before numpy is enough
