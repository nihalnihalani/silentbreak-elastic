"""RootCause pure logic — mapping diff names the rename; dollar exposure is
computed from nulled rows x baseline average and labeled "estimated".
"""
from agents import rootcause


class FakeContradiction:
    metric = "null_rate"
    value = 1.0
    baseline_mean = 0.002
    z = 40.0
    pipeline_status = "SUCCESS"


class FakeClient:
    def __init__(self, today_fields, yesterday_fields,
                 null_rate=1.0, row_count=10_000, baseline_avg=43.0):
        self._mappings = {"today": set(today_fields), "yesterday": set(yesterday_fields)}
        self._stats = {"null_rate": null_rate, "row_count": row_count}
        self._baseline_avg = baseline_avg

    def get_mappings(self, index):
        return set(self._mappings["today" if index == "sales-today" else "yesterday"])

    def esql_stats(self, index):
        return dict(self._stats)

    def get_baseline(self, metric):
        assert metric == "avg_amount"
        return {"mean": self._baseline_avg, "std": 0.5}


def test_mapping_diff_names_removed_and_added_fields():
    client = FakeClient(
        today_fields={"order_id", "sku", "gross_amount"},
        yesterday_fields={"order_id", "sku", "amount"},
    )
    d = rootcause.diagnose(client, "sales-today", "sales-yesterday", FakeContradiction())
    assert d.removed == {"amount"}
    assert d.added == {"gross_amount"}
    assert "`amount`" in d.sentence and "`gross_amount`" in d.sentence


def test_dollar_exposure_is_estimate_of_nulled_rows_times_baseline():
    client = FakeClient(
        today_fields={"order_id", "sku", "gross_amount"},
        yesterday_fields={"order_id", "sku", "amount"},
        null_rate=0.5, row_count=10_000, baseline_avg=43.0,
    )
    d = rootcause.diagnose(client, "sales-today", "sales-yesterday", FakeContradiction())
    assert d.nulled_rows == 5_000
    assert d.estimated_impact_usd == 5_000 * 43.0
    # The impact must always be labeled an estimate, never stated as fact.
    assert "estimated" in d.sentence.lower()


def test_no_schema_change_falls_back_to_investigate():
    same = {"order_id", "sku", "amount"}
    client = FakeClient(today_fields=same, yesterday_fields=same)
    d = rootcause.diagnose(client, "sales-today", "sales-yesterday", FakeContradiction())
    assert d.removed == set() and d.added == set()
    assert "investigate" in d.sentence.lower()


def test_as_event_has_stable_keys_and_sorted_fields():
    client = FakeClient(
        today_fields={"order_id", "b_new", "a_new"},
        yesterday_fields={"order_id", "amount"},
    )
    d = rootcause.diagnose(client, "sales-today", "sales-yesterday", FakeContradiction())
    event = d.as_event()
    assert {"sentence", "removed_fields", "added_fields",
            "estimated_impact_usd", "nulled_rows"} <= set(event)
    assert event["added_fields"] == sorted(event["added_fields"])
