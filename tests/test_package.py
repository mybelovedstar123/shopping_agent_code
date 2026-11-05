import re

import kyo


def test_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", kyo.__version__)
