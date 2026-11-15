from rag_agent.citations import parse_markers, split_units, ungrounded_numbers, validate


def _v(text, k=5):
    v = validate(text, k)
    return v.status, v.subtype, v.reason, v.cited


def test_e1_markers_at_sentence_end():
    text = "The maximum TDS ratio is 44% [S1]. Exceptions above that need credit approval [S2][S3]."
    assert _v(text) == ("answered", None, None, [1, 2, 3])


def test_e2_markers_after_the_period_are_repaired():
    text = "The maximum TDS ratio is 44%. [S1] Exceptions need credit approval. [S2]"
    assert _v(text) == ("answered", None, None, [1, 2])


def test_e3_uncited_sentence():
    text = "The maximum TDS ratio is 44% [S1]. Ask your broker for the details."
    assert _v(text)[:3] == ("fallback", "sources_only", "uncited")


def test_e4_abbreviation_does_not_split():
    assert _v("Fees apply, e.g. appraisal and legal fees [S2].")[0] == "answered"


def test_e5_marker_out_of_range():
    assert _v("The rate resets every year [S4].", k=3)[:3] == (
        "fallback",
        "sources_only",
        "citation_invalid",
    )


def test_e6_comma_marker():
    assert _v("Both products allow prepayment [S1, S3].") == ("answered", None, None, [1, 3])


def test_e7_declined():
    assert _v("INSUFFICIENT_CONTEXT")[:3] == ("fallback", "no_basis", "model_declined")


def test_empty_output():
    assert _v("  \n ")[:3] == ("fallback", "sources_only", "empty_output")


def test_marker_zero_is_invalid():
    assert _v("The maximum TDS ratio is 44% [S0].")[2] == "citation_invalid"


def test_marker_only_text_is_uncited():
    assert _v("[S1]")[2] == "uncited"


def test_bullets_and_numbering_are_units():
    text = (
        "- The first limit applies to all files [S1]\n"
        "* The second limit applies too [S2]\n"
        "3. A third item is listed here [S1]"
    )
    assert split_units(text) == [
        "The first limit applies to all files [S1]",
        "The second limit applies too [S2]",
        "A third item is listed here [S1]",
    ]


def test_parse_markers():
    assert parse_markers("Text [S1][S2] more [ S3 , S4 ].") == [1, 2, 3, 4]


def test_e8_ungrounded_number():
    units = ["Each quarterly payment is 351.75 [S1]."]
    flagged = ungrounded_numbers(units, {1: "The rate is 4.69% a year."}, "How much is 30,000?")
    assert len(flagged) == 1


def test_numbers_from_question_and_cited_text_are_grounded():
    units = ["A loan of 30,000 at 4.69% is allowed [S1]."]
    assert ungrounded_numbers(units, {1: "The rate is 4.69% a year."}, "What about 30,000?") == []


def test_number_must_be_in_a_cited_chunk():
    units = ["The rate is 4.69% a year here [S1]."]
    assert len(ungrounded_numbers(units, {1: "nothing", 2: "4.69%"}, "q")) == 1
