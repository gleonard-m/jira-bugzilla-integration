import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jbi.app import app, traces_sampler
from jbi.environment import get_settings
from jbi.models import BugzillaWebhookRequest


def test_request_summary_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as anon_client:
            # https://fastapi.tiangolo.com/advanced/testing-events/
            anon_client.get(
                "/",
                headers={
                    "X-Request-Id": "foo-bar",
                },
            )

            summary = [r for r in caplog.records if r.name == "request.summary"][0]

            assert summary.rid == "foo-bar"
            assert summary.method == "GET"
            assert summary.path == "/"
            assert summary.querystring == "{}"


def test_request_summary_defaults_user_agent_to_empty_string(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as anon_client:
            del anon_client.headers["User-Agent"]
            anon_client.get("/")

            summary = [r for r in caplog.records if r.name == "request.summary"][0]

            assert summary.agent == ""


@pytest.mark.parametrize(
    "sampling_context,expected",
    [
        # /__lbheartbeat__
        ({"asgi_scope": {"path": "/__lbheartbeat__"}}, 0),
        # path that isn't /__lbheartbeat__
        (
            {"asgi_scope": {"path": "/"}},
            get_settings().sentry_traces_sample_rate,
        ),
        # context w/o an asgi_scope
        (
            {"parent_sampled": None},
            get_settings().sentry_traces_sample_rate,
        ),
        # context w/o an asgi_scope.path
        (
            {"asgi_scope": {"type": "lifespan"}},
            get_settings().sentry_traces_sample_rate,
        ),
    ],
)
def test_traces_sampler(sampling_context, expected):
    assert traces_sampler(sampling_context) == expected


def test_errors_are_reported_to_sentry(
    anon_client, webhook_create_example: BugzillaWebhookRequest
):
    with patch("sentry_sdk.hub.Hub.capture_event") as mocked:
        with patch("jbi.router.execute_action", side_effect=ValueError):
            with pytest.raises(ValueError):
                anon_client.post(
                    "/bugzilla_webhook", data=webhook_create_example.json()
                )

    assert mocked.called, "Sentry captured the exception"


def test_request_id_is_passed_down_to_logger_contexts(
    caplog,
    webhook_create_example: BugzillaWebhookRequest,
    mocked_jira,
    mocked_bugzilla,
):
    mocked_bugzilla.get_bug.return_value = webhook_create_example.bug
    mocked_jira.create_issue.return_value = {
        "key": "JBI-1922",
    }
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as anon_client:
            anon_client.post(
                "/bugzilla_webhook",
                data=webhook_create_example.json(),
                headers={
                    "X-Request-Id": "foo-bar",
                },
            )

    runner_logs = [r for r in caplog.records if r.name == "jbi.runner"]
    assert runner_logs[0].rid == "foo-bar"
