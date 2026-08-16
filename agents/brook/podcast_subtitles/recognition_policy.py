"""Closed production policy for independent Recognition hypotheses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import hash_object
from .ports import RecognitionModelIdentity


class _PolicyContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RecognitionFamilyBindingV1(_PolicyContract):
    ordinal: int = Field(ge=0)
    role: Literal["primary", "corroborating"]
    identity_hash: str
    model_family: Literal["qwen3-asr", "whisper"]
    inference_runtime_family: Literal["qwen-asr-transformers", "ctranslate2"]
    decoder_family: Literal["qwen3-asr-generative", "whisper-sequence-decoder"]


class RecognitionIndependenceReceiptV1(_PolicyContract):
    schema_version: Literal[1] = 1
    policy_id: Literal["nakama-independent-primary-secondary-v1"] = (
        "nakama-independent-primary-secondary-v1"
    )
    bindings: tuple[RecognitionFamilyBindingV1, ...]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _closed_content_hash(self) -> RecognitionIndependenceReceiptV1:
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != hash_object(payload):
            raise ValueError("Recognition independence receipt content hash mismatch")
        return self


ModelFamily: TypeAlias = Literal["qwen3-asr", "whisper"]
RuntimeFamily: TypeAlias = Literal["qwen-asr-transformers", "ctranslate2"]
DecoderFamily: TypeAlias = Literal[
    "qwen3-asr-generative",
    "whisper-sequence-decoder",
]
RecognitionFamily: TypeAlias = tuple[ModelFamily, RuntimeFamily, DecoderFamily]


def _family(identity: RecognitionModelIdentity) -> RecognitionFamily:
    if identity.adapter_name == "qwen3-asr-forced-alignment" and identity.model.startswith(
        "Qwen/Qwen3-ASR-"
    ):
        return ("qwen3-asr", "qwen-asr-transformers", "qwen3-asr-generative")
    if (
        identity.adapter_name == "faster-whisper-word-timestamps"
        and identity.model == "Systran/faster-whisper-large-v3"
    ):
        return ("whisper", "ctranslate2", "whisper-sequence-decoder")
    raise ValueError(
        "production Recognition identity has no closed model/runtime/decoder family proof"
    )


@dataclass(frozen=True, slots=True)
class RecognitionIndependencePolicyV1:
    id: Literal["nakama-independent-primary-secondary-v1"] = (
        "nakama-independent-primary-secondary-v1"
    )

    def validate(
        self,
        identities: tuple[RecognitionModelIdentity, ...],
    ) -> RecognitionIndependenceReceiptV1:
        if len(identities) != 2:
            raise ValueError(
                "production Recognition requires exactly one primary and one independent secondary"
            )
        families = tuple(_family(identity) for identity in identities)
        if families[0] != (
            "qwen3-asr",
            "qwen-asr-transformers",
            "qwen3-asr-generative",
        ) or families[1] != (
            "whisper",
            "ctranslate2",
            "whisper-sequence-decoder",
        ):
            raise ValueError(
                "production Recognition roles must be Qwen primary then "
                "Faster-Whisper corroborating"
            )
        bindings = tuple(
            RecognitionFamilyBindingV1(
                ordinal=index,
                role="primary" if index == 0 else "corroborating",
                identity_hash=identity.content_hash,
                model_family=family[0],
                inference_runtime_family=family[1],
                decoder_family=family[2],
            )
            for index, (identity, family) in enumerate(zip(identities, families))
        )
        payload = {
            "schema_version": 1,
            "policy_id": self.id,
            "bindings": bindings,
        }
        return RecognitionIndependenceReceiptV1(
            **payload,
            content_hash=hash_object(payload),
        )

    @property
    def content_hash(self) -> str:
        return hash_object({"schema_version": 1, "id": self.id})


PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY = RecognitionIndependencePolicyV1()


__all__ = [
    "PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY",
    "RecognitionFamilyBindingV1",
    "RecognitionIndependencePolicyV1",
    "RecognitionIndependenceReceiptV1",
]
