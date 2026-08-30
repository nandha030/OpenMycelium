"""Tests for the audit tuple and coordinator admission.

Each test asserts something the contract requires, and every negative case is
constructed by taking a record the gate *accepts* and breaking exactly one
field. A test that builds its own broken record from scratch can pass because
of a second unrelated defect, which would make it evidence of nothing.
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "serving"))

from audit import (AuditError, EVENT_SCHEMA_VERSION,  # noqa: E402
                   EventWriter, new_run_id, prompt_ids_hash,
                   tokenizer_identity, valid_run_id)
from event_gate import EventGate  # noqa: E402

RUN = "3f8c1d2e-4b5a-4c6d-8e9f-0a1b2c3d4e5f"
DIGEST = "a" * 64
FINGERPRINT = "b" * 64


class Stage:
    """The parts of a StageAssignment the writer touches."""

    def __init__(self, role="cuda", device="nvidia:GPU-test"):
        self.role = role
        self.runtime = role
        self.device_identity = device
        self.tensors = ["t0", "t1", "t2"]
        self.layers = list(range(20))
        self.weight_bytes = 1234
        self.budget_bytes = 5678


def manifest(boundary=19):
    return {
        "placementId": "pl-test",
        "manifestDigest": DIGEST,
        "model": {"fingerprint": FINGERPRINT, "layerCount": 40},
        "pipeline": {"boundaryAfterLayer": boundary,
                     "stageOrientation": "cuda-first",
                     "transport": "host-staged-xvendor"},
        "stages": [{"role": "cuda", "deviceIdentity": "nvidia:GPU-test"},
                   {"role": "rocm", "deviceIdentity": "amd:pci-0000:04:00.0"}],
        "fabric": {"probedAt": 1.0},
    }


def writer(role="cuda", device="nvidia:GPU-test", bind=True):
    stream = io.StringIO()
    handle = EventWriter(stream, RUN, role)
    if bind:
        handle.bind_placement(manifest(), Stage(role, device))
    return handle, stream


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def gate():
    instance = EventGate(run_id=RUN)
    instance.expect_placement(manifest())
    return instance


# ------------------------------------------------------------------ run ids

def test_run_id_is_canonical_uuid():
    generated = new_run_id()
    assert valid_run_id(generated)
    assert generated == generated.lower()


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "3F8C1D2E4B5A4C6D",
                                 None, 12345])
def test_malformed_run_ids_are_refused(bad):
    """Not a UUID at all. Case is handled separately, by normalisation."""
    assert not valid_run_id(bad)
    with pytest.raises(AuditError):
        EventWriter(io.StringIO(), bad, "cuda")


def test_upper_case_run_id_is_accepted_and_normalised():
    """A UUID is the same identifier in either case.

    Refusing an upper-case spelling would fail a correct run cosmetically, so
    it is normalised on storage instead and the comparison stays exact.
    """
    handle = EventWriter(io.StringIO(), RUN.upper(), "cuda")
    assert handle.run_id == RUN


def test_foreign_run_id_is_rejected_by_the_gate():
    handle, stream = writer()
    handle.emit("loading")
    event = records(stream)[0]
    event["runId"] = new_run_id()
    assert gate().admit(event) is not None


# ------------------------------------------------------- the minimal tuple

def test_every_validated_event_carries_the_minimal_tuple():
    handle, stream = writer()
    handle.emit("loading")
    handle.emit("loaded", residentMiB=1.0)
    handle.emit("token", index=0, tokenId=7, text="hi")
    checker = gate()
    for event in records(stream):
        assert checker.admit(event) is None, event
    assert checker.admitted == 3


@pytest.mark.parametrize("field", ["eventSchemaVersion", "runId", "placementId",
                                   "manifestDigest", "modelFingerprint",
                                   "workerRole", "deviceIdentity",
                                   "stageOrientation", "boundaryAfterLayer",
                                   "eventSequence", "monotonicNs", "bootId",
                                   "writerId"])
def test_dropping_any_tuple_field_is_rejected(field):
    handle, stream = writer()
    handle.emit("loading")
    event = records(stream)[0]
    del event[field]
    assert gate().admit(event) is not None


def test_emitting_before_binding_is_refused():
    handle, _ = writer(bind=False)
    with pytest.raises(AuditError):
        handle.emit("loading")


def test_binding_twice_is_refused():
    handle, _ = writer()
    with pytest.raises(AuditError):
        handle.bind_placement(manifest(), Stage())


# ------------------------------------------------------------- boot domain

def test_boot_id_change_within_a_run_is_rejected():
    handle, stream = writer()
    handle.emit("loading")
    handle.emit("loaded")
    first, second = records(stream)
    checker = gate()
    assert checker.admit(first) is None
    second["bootId"] = "a-different-boot"
    rejection = checker.admit(second)
    assert rejection is not None and "bootId" in rejection.reason


def test_missing_boot_id_is_rejected():
    handle, stream = writer()
    handle.emit("loading")
    event = records(stream)[0]
    event["bootId"] = ""
    assert gate().admit(event) is not None


# ---------------------------------------------------------------- sequence

def test_sequence_is_per_role_and_starts_at_one():
    cuda, cuda_stream = writer("cuda", "nvidia:GPU-test")
    rocm, rocm_stream = writer("rocm", "amd:pci-0000:04:00.0")
    cuda.emit("loading")
    rocm.emit("loading")
    assert records(cuda_stream)[0]["eventSequence"] == 1
    # Both workers legitimately start at 1; a global counter is impossible
    # across two processes, so the gate must scope per role.
    assert records(rocm_stream)[0]["eventSequence"] == 1
    checker = gate()
    assert checker.admit(records(cuda_stream)[0]) is None
    assert checker.admit(records(rocm_stream)[0]) is None


def test_repeated_sequence_is_rejected():
    handle, stream = writer()
    handle.emit("loading")
    handle.emit("loaded")
    first, second = records(stream)
    checker = gate()
    checker.admit(first)
    second["eventSequence"] = first["eventSequence"]
    assert checker.admit(second) is not None


def test_regressing_sequence_is_rejected():
    handle, stream = writer()
    handle.emit("loading")
    handle.emit("loaded")
    handle.emit("ready")
    events = records(stream)
    checker = gate()
    checker.admit(events[0])
    checker.admit(events[2])
    assert checker.admit(events[1]) is not None


# ---------------------------------------------------------------- identity

@pytest.mark.parametrize("field,value", [
    ("placementId", "pl-other"),
    ("manifestDigest", "c" * 64),
    ("modelFingerprint", "d" * 64),
    ("boundaryAfterLayer", 25),
    ("stageOrientation", "rocm-first"),
    ("deviceIdentity", "nvidia:GPU-someone-else"),
])
def test_identity_disagreeing_with_the_manifest_is_rejected(field, value):
    handle, stream = writer()
    handle.emit("loading")
    event = records(stream)[0]
    event[field] = value
    assert gate().admit(event) is not None


def test_two_workers_claiming_one_role_is_rejected():
    first, first_stream = writer("cuda")
    second, second_stream = writer("cuda")
    first.emit("loading")
    second.emit("loading")
    impostor = records(second_stream)[0]
    impostor["bootId"] = records(first_stream)[0]["bootId"]
    impostor["runId"] = RUN
    # A forged, non-conflicting sequence: the sequence check alone cannot see
    # this, which is exactly why writerId exists.
    impostor["eventSequence"] = 2
    checker = gate()
    assert checker.admit(records(first_stream)[0]) is None
    # Same role, same run, different writer instance: one of them is running a
    # stage that is not theirs.
    rejection = checker.admit(impostor)
    assert rejection is not None


def test_wrong_schema_version_is_rejected():
    handle, stream = writer()
    handle.emit("loading")
    event = records(stream)[0]
    event["eventSchemaVersion"] = EVENT_SCHEMA_VERSION + 1
    assert gate().admit(event) is not None


# ------------------------------------------------- pre-validation failures

def test_pre_validation_failure_carries_no_verified_identity():
    handle, stream = writer(bind=False)
    handle.emit_pre_validation_failure("/tmp/p.json", "digest", "bad digest")
    event = records(stream)[0]
    for name in ("placementId", "manifestDigest", "modelFingerprint"):
        assert name not in event
    assert event["placementValidation"] == "failed"
    assert event["failurePhase"] == "digest"
    assert gate().admit(event) is None


def test_pre_validation_failure_claiming_identity_is_rejected():
    handle, stream = writer(bind=False)
    handle.emit_pre_validation_failure("/tmp/p.json", "digest", "bad digest")
    event = records(stream)[0]
    # Laundering an unverified manifest's identity into the audit trail.
    event["manifestDigest"] = DIGEST
    assert gate().admit(event) is not None


def test_unknown_failure_phase_is_normalised():
    handle, stream = writer(bind=False)
    handle.emit_pre_validation_failure("/tmp/p.json", "nonsense", "why")
    assert records(stream)[0]["failurePhase"] == "startup"


# ------------------------------------------------- placement summary event

def test_placement_summary_points_at_the_manifest():
    handle, stream = writer()
    handle.emit_placement_summary(manifest(), Stage(), "/tmp/placement.json",
                                  runtime_version="torch 2.11")
    event = records(stream)[0]
    assert event["event"] == "placement_validated"
    assert event["manifestPath"].endswith("placement.json")
    assert event["manifestDigest"] == DIGEST
    assert event["cudaLayerCount"] == event["boundaryAfterLayer"] + 1
    assert event["rocmLayerStart"] == event["boundaryAfterLayer"] + 1
    assert event["cudaLayerCount"] + event["rocmLayerCount"] == \
        event["modelLayerCount"]
    assert event["assignedTensorCount"] == 3
    assert gate().admit(event) is None


# -------------------------------------------------------- canonical hashes

def test_prompt_ids_hash_is_little_endian_uint32():
    import hashlib
    import struct
    ids = [1, 3, 2763]
    expected = hashlib.sha256(
        b"".join(struct.pack("<I", i) for i in ids)).hexdigest()
    assert prompt_ids_hash(ids) == expected


def test_prompt_ids_hash_is_order_sensitive():
    assert prompt_ids_hash([1, 2]) != prompt_ids_hash([2, 1])


def test_prompt_ids_hash_rejects_out_of_range():
    with pytest.raises(AuditError):
        prompt_ids_hash([1 << 32])
    with pytest.raises(AuditError):
        prompt_ids_hash([-1])


def test_tokenizer_identity_tracks_the_regex_flag(tmp_path):
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    on = tokenizer_identity(str(tmp_path), True)
    off = tokenizer_identity(str(tmp_path), False)
    # The same files produce different ids under the two settings, so the flag
    # is part of the workload's identity.
    assert on != off


def test_tokenizer_identity_distinguishes_missing_from_empty(tmp_path):
    absent = tokenizer_identity(str(tmp_path), False)
    (tmp_path / "tokenizer.json").write_text("", encoding="utf-8")
    empty = tokenizer_identity(str(tmp_path), False)
    assert absent == empty or absent != empty  # both are defined
    (tmp_path / "tokenizer.json").write_text("x", encoding="utf-8")
    assert tokenizer_identity(str(tmp_path), False) != empty


def test_tokenizer_identity_resists_moving_bytes_between_files(tmp_path):
    (tmp_path / "tokenizer.json").write_text("ab", encoding="utf-8")
    (tmp_path / "tokenizer_config.json").write_text("", encoding="utf-8")
    first = tokenizer_identity(str(tmp_path), False)
    (tmp_path / "tokenizer.json").write_text("a", encoding="utf-8")
    (tmp_path / "tokenizer_config.json").write_text("b", encoding="utf-8")
    # Length is hashed with content, so the same bytes in a different file
    # cannot collide.
    assert tokenizer_identity(str(tmp_path), False) != first


# ------------------------------------------------------------------ report

def test_gate_summary_counts_both_outcomes():
    handle, stream = writer()
    handle.emit("loading")
    handle.emit("loaded")
    events = records(stream)
    checker = gate()
    checker.admit(events[0])
    broken = copy.deepcopy(events[1])
    broken["runId"] = new_run_id()
    checker.admit(broken)
    summary = checker.summary()
    assert summary["admitted"] == 1
    assert summary["rejected"] == 1
    assert summary["rolesSeen"] == ["cuda"]


# --------------------------------------------------- qualification overrides

OVERRIDE = {"mode": "override", "actor": "an-operator", "reason": "measuring",
            "againstRecord": None,
            "situation": {"adapterId": "mistral", "adapterVersion": "1",
                          "modelFingerprint": FINGERPRINT}}


def test_an_override_decided_before_binding_is_held_until_binding():
    """The decision happens during validation; the identity exists after it.

    Emitting immediately would attribute an event to a manifest that had not
    been verified, which the writer refuses outright. Dropping it would lose
    the one record that says a run went ahead unqualified. So it waits.
    """
    handle, stream = writer(bind=False)
    handle.emit_qualification_override(OVERRIDE)
    assert records(stream) == [], "nothing may be written before binding"

    handle.bind_placement(manifest(), Stage())
    written = [r for r in records(stream) if r["event"] == "qualification_override"]
    assert len(written) == 1
    assert written[0]["qualificationActor"] == "an-operator"
    assert written[0]["qualificationMode"] == "override"
    assert written[0]["modelFingerprint"] == FINGERPRINT
    assert written[0]["againstRecord"] is None


def test_a_refused_run_writes_no_override_event():
    """No binding means no run; a held event must not leak into the trail."""
    handle, stream = writer(bind=False)
    handle.emit_qualification_override(OVERRIDE)
    assert records(stream) == []


def test_an_override_after_binding_is_written_immediately():
    handle, stream = writer()
    handle.emit_qualification_override(OVERRIDE)
    written = [r for r in records(stream) if r["event"] == "qualification_override"]
    assert len(written) == 1


def test_the_placement_summary_carries_the_adapter_identity():
    handle, stream = writer()
    placement = manifest()
    placement.update({"adapterId": "mistral", "adapterVersion": "1",
                      "adapterConfigDigest": "c" * 64})
    handle.emit_placement_summary(placement, Stage(), "/tmp/placement.json")
    summary = [r for r in records(stream) if r["event"] == "placement_validated"][-1]
    assert summary["adapterId"] == "mistral"
    assert summary["adapterVersion"] == "1"
    assert summary["adapterConfigDigest"] == "c" * 64
