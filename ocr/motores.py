"""Motores de OCR (docs/0019): Tesseract, para todas as páginas, e RapidOCR, para a segunda leitura.

Cada motor recebe uma imagem RGB do Pillow e devolve uma Leitura. A confiança é a média das
palavras (Tesseract) ou linhas (RapidOCR) ponderada pelo tamanho do texto, de 0 a 1.
"""

import functools
import os
import time
from dataclasses import dataclass

THREADS = int(os.environ.get('OCR_THREADS', '1'))  # por processo; o paralelismo vem da concorrência do worker


@dataclass(frozen=True)
class Leitura:
    texto: str
    confianca: float  # 0 a 1
    motor: str  # 'tesseract' ou 'rapid'
    segundos: float


def tesseract(imagem, idioma: str = 'por') -> Leitura:
    import pytesseract

    inicio = time.perf_counter()
    dados = pytesseract.image_to_data(imagem, lang=idioma, output_type=pytesseract.Output.DICT)
    linhas, pesos = {}, []
    for i, palavra in enumerate(dados['text']):
        conf = float(dados['conf'][i])
        if conf < 0 or not palavra.strip():  # -1: bloco/linha, não palavra
            continue
        linhas.setdefault((dados['block_num'][i], dados['par_num'][i], dados['line_num'][i]), []).append(palavra)
        pesos.append((conf / 100, len(palavra)))
    texto, bloco_anterior = [], None
    for (bloco, _, _), palavras in sorted(linhas.items()):
        if bloco_anterior is not None and bloco != bloco_anterior:
            texto.append('')  # linha em branco entre blocos, como nos parágrafos do texto nativo
        texto.append(' '.join(palavras))
        bloco_anterior = bloco
    return Leitura('\n'.join(texto), _media(pesos), 'tesseract', time.perf_counter() - inicio)


def rapid(imagem) -> Leitura:
    import numpy as np

    inicio = time.perf_counter()
    resultado = _motor_rapid()(np.asarray(imagem)[:, :, ::-1])  # o RapidOCR espera BGR (OpenCV)
    textos = list(resultado.txts or ())
    pesos = [(float(s), len(t)) for t, s in zip(textos, resultado.scores or ()) if t.strip()]
    return Leitura('\n'.join(textos), _media(pesos), 'rapid', time.perf_counter() - inicio)


@functools.cache
def _motor_rapid():
    """Carregado uma vez por processo (os modelos ONNX levam alguns segundos para abrir)."""
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

    return RapidOCR(params={
        'Global.use_cls': False,  # páginas de processo vêm na orientação certa
        'Global.log_level': 'warning',
        'Det.lang_type': LangDet.CH, 'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.MOBILE,
        'Rec.lang_type': LangRec.LATIN, 'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE,
        'EngineConfig.onnxruntime.intra_op_num_threads': THREADS,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })


def _media(pesos):
    total = sum(n for _, n in pesos)
    return round(sum(c * n for c, n in pesos) / total, 3) if total else 0.0
