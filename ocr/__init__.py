"""Reconhecimento de texto (OCR) de páginas: imagem da página → texto + confiança.

Biblioteca sem Django e sem banco: não conhece lote, arquivo nem processo. A orquestração (quais
páginas, em que ordem, onde gravar) fica no app `ingestao` (ver ingestao/docs/0018 e 0019).

As dependências pesadas (pypdfium2, Tesseract, RapidOCR) só existem na imagem "ocr" do
Dockerfile e são importadas na hora do uso: `import ocr` funciona em qualquer imagem.
"""

from .imagem import DPI, paginas
from .motores import Leitura, rapid, tesseract

__all__ = ['DPI', 'Leitura', 'paginas', 'rapid', 'tesseract']
