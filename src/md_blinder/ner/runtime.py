"""Сессия ONNX Runtime для GLiNER (порт src/workers/glinerRuntime.ts).

Только CPU (CPUExecutionProvider), веса fp16 — как в браузере. int8/q4f16 не
используются: на них настоящие русские имена получали ~0.16 вместо ~0.92.

Оптимизация графа. В браузере (onnxruntime-web, WASM) уровень 'all' падал в
SimplifiedLayerNormFusion на этом fp16-графе (onnxruntime #26717), поэтому там
стоит 'layout'. В нативном onnxruntime 1.31 на CPU все уровни (disabled, basic,
extended, layout, all) создают сессию, проходят калибровку и дают одни и те же
спаны. Выбран ORT_ENABLE_ALL с отключенной только SimplifiedLayerNormFusion:
по скорости он неотличим от полного 'all' (в пределах шума замеров, ~15–20%
быстрее basic/extended/layout), но оценки отличаются от неоптимизированного графа
не более чем на ~0.001, тогда как с этой фьюжн — до 0.026 (ООО «Альфа»), то есть
заметно ближе к браузерному 'layout'. Если будущая версия ORT сломает граф,
engine.load_model перебирает GRAPH_OPTIMIZATION_FALLBACKS — каждый вариант обязан
пройти калибровку.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .gliner_span import Span, decode_spans, prepare_span_batch

CLS_ID = 1
SEP_ID = 2
ENT_ID = 250103
PROMPT_SEP_ID = 250104

SIMPLIFIED_LAYER_NORM_FUSION = "SimplifiedLayerNormFusion"


@dataclass(frozen=True)
class OptimizationConfig:
    level: str
    disabled_optimizers: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.level + "".join(f" -{name}" for name in self.disabled_optimizers)


# Порядок важен: первым — выбранный вариант, дальше — запасные, более консервативные.
GRAPH_OPTIMIZATION_FALLBACKS: tuple[OptimizationConfig, ...] = (
    OptimizationConfig("all", (SIMPLIFIED_LAYER_NORM_FUSION,)),
    OptimizationConfig("basic"),
    OptimizationConfig("disabled"),
)
_LEVELS = {
    "disabled": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
    "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
    "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
    "layout": ort.GraphOptimizationLevel.ORT_ENABLE_LAYOUT,
    "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
}


class NerRuntimeError(RuntimeError):
    pass


@dataclass
class _WordTokenizer:
    tokenizer: Tokenizer
    cls_id: int = CLS_ID
    sep_id: int = SEP_ID

    def encode_word(self, word: str) -> list[int]:
        return self.tokenizer.encode(word, add_special_tokens=False).ids


def load_tokenizer(model_dir: Path) -> _WordTokenizer:
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    ids = [tokenizer.token_to_id(t) for t in ("[CLS]", "[SEP]", "<<ENT>>", "<<SEP>>")]
    if ids != [CLS_ID, SEP_ID, ENT_ID, PROMPT_SEP_ID]:
        raise NerRuntimeError("Токенизатор несовместим с закреплённой моделью NER")
    return _WordTokenizer(tokenizer)


class GlinerModel:
    def __init__(
        self,
        model_dir: Path | str,
        *,
        optimization: OptimizationConfig = GRAPH_OPTIMIZATION_FALLBACKS[0],
        intra_op_threads: int = 0,
        tokenizer: Optional[_WordTokenizer] = None,
    ) -> None:
        model_dir = Path(model_dir)
        self.model_dir = model_dir
        self.optimization = optimization
        self.tokenizer = tokenizer or load_tokenizer(model_dir)
        options = ort.SessionOptions()
        options.graph_optimization_level = _LEVELS[optimization.level]
        options.log_severity_level = 3  # шум оптимизатора («Could not find a CPU kernel…») не ошибка
        if intra_op_threads:
            options.intra_op_num_threads = intra_op_threads
        kwargs = {"disabled_optimizers": list(optimization.disabled_optimizers)} if optimization.disabled_optimizers else {}
        try:
            # Путь, а не байты: 580 МБ не дублируются в памяти Python.
            self._session: Optional[ort.InferenceSession] = ort.InferenceSession(
                str(model_dir / "model_fp16.onnx"), options, providers=["CPUExecutionProvider"], **kwargs
            )
        except Exception as error:  # noqa: BLE001 — ORT бросает свои классы исключений
            raise NerRuntimeError(f"Не удалось создать сессию NER ({optimization}): {error}") from error

    def infer(self, texts: Sequence[str], labels: Sequence[str], threshold: float = 0.5) -> list[list[Span]]:
        if not texts:
            return []
        if self._session is None:
            raise NerRuntimeError("Сессия NER уже закрыта")
        batch = prepare_span_batch(texts, labels, self.tokenizer)
        feeds = {
            "input_ids": batch.input_ids,
            "attention_mask": batch.attention_mask,
            "words_mask": batch.words_mask,
            "text_lengths": batch.text_lengths,
            "span_idx": batch.span_idx,
            "span_mask": batch.span_mask,
        }
        (logits,) = self._session.run(["logits"], feeds)
        if not isinstance(logits, np.ndarray) or logits.dtype not in (np.float32, np.float64):
            raise NerRuntimeError("Модель NER вернула неподдерживаемый результат")
        expected_shape = (batch.batch_size, batch.word_count, batch.max_width, len(labels))
        if tuple(logits.shape) != expected_shape:
            raise NerRuntimeError("Модель NER вернула неверную форму результата")
        return decode_spans(logits, batch, texts, labels, threshold)

    def release(self) -> None:
        self._session = None
