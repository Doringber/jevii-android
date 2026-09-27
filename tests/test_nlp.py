import pytest

from jevii_android.nlp import NlpCompileError, compile_story


def test_story_compiles_to_executable_steps_without_dropping_actions():
    case = compile_story({
        "name": "movie handoff",
        "apps": {"IMDb": "com.imdb.mobile", "Box": "com.box.gallery"},
        "story": '''
        Restart IMDb to start clean.
        If visible, tap "NOT NOW".
        Search for "The Martian".
        Open the 2015 movie title.
        Switch to Box.
        Return to IMDb.
        Restart IMDb.
        Verify the home screen shows "Search for shows, movies, people…".
        ''',
    })

    operations = [next(iter(step)) for step in case["steps"]]
    assert operations == [
        "relaunch_app", "tap_text", "goal", "type_text", "enter", "assert_text",
        "goal", "assert_text", "open_app", "open_app", "relaunch_app", "assert_text",
    ]
    assert case["steps"][1]["optional"] is True
    assert case["steps"][1]["timeout_seconds"] == 0.3
    assert case["steps"][-1]["assert_text"] == "Search for shows, movies, people…"


def test_story_rejects_unknown_instruction_instead_of_skipping_it():
    with pytest.raises(NlpCompileError) as error:
        compile_story({
            "name": "unsupported",
            "apps": {"IMDb": "com.imdb.mobile"},
            "story": "Open IMDb. Buy the movie ticket.",
        })
    assert error.value.issues == [{
        "code": "UNSUPPORTED_INSTRUCTION",
        "instruction": "Buy the movie ticket",
        "message": "No action was generated for this instruction. Rewrite it using a supported action or split it into a Jev goal.",
    }]


def test_story_requires_app_package_mapping():
    with pytest.raises(ValueError, match=r"\[apps\]"):
        compile_story({"name": "missing apps", "story": "Open IMDb."})


def test_app_search_selector_avoids_a_model_goal_for_known_controls():
    case = compile_story({
        "name": "fast search",
        "apps": {"IMDb": {
            "package": "com.imdb.mobile",
            "activity": ".HomeActivity",
            "search_text": "Search IMDb",
        }},
        "story": "Open IMDb. Search for \"The Martian\".",
    })
    assert case["steps"][0] == {
        "open_app": "com.imdb.mobile",
        "activity": ".HomeActivity",
        "timeout_seconds": 4,
    }
    assert case["steps"][1] == {"tap_text": "Search IMDb", "timeout_seconds": 2}


def test_story_can_open_a_url_in_a_mapped_browser():
    case = compile_story({
        "name": "browser handoff",
        "apps": {"Chrome": "com.android.chrome"},
        "story": "Visit https://example.com in Chrome.",
    })
    assert case["steps"] == [{
        "open_url": "https://example.com",
        "timeout_seconds": 4,
        "package": "com.android.chrome",
    }]
