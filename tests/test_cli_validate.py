"""parse_verdict: tolerant last-line token parse + priority."""
from workagent import cli_validate as cv


def test_job_done_exact():
    token, mode = cv.parse_verdict("did the work\nJOB_DONE")
    assert (token, mode) == ("JOB_DONE", "exact")


def test_tolerant_quotes_and_case():
    token, mode = cv.parse_verdict('done!\n"job_done"')
    assert token == "JOB_DONE" and mode == "tolerant"


def test_fenced_token():
    token, _ = cv.parse_verdict("finishing\n```\nINCOMPLETE_OR_FAILED_JOB\n```")
    assert token == "INCOMPLETE_OR_FAILED_JOB"


def test_mid_transcript_token_ignored():
    token, mode = cv.parse_verdict("JOB_DONE\nthen I broke it again")
    assert (token, mode) == (None, "none")


def test_priority_waiting_over_done():
    assert cv.pick_verdict(["JOB_DONE", "WAITING_USER_RESPONSE"]) == "WAITING_USER_RESPONSE"


def test_all_five_tokens_known():
    assert set(cv.VERDICT_TOKENS) == {"JOB_DONE", "INCOMPLETE_OR_FAILED_JOB",
        "INTENTIONALLY_UNCOMMITTED_CHANGES", "WAITING_USER_RESPONSE",
        "MAX_RETRIES_EXCEEDED"}
