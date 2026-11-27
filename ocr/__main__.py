"""`python -m ocr`: lê uma página de teste com os dois motores e mostra o resultado.

Usado no build da imagem "ocr" (Dockerfile): o RapidOCR só baixa os modelos na primeira leitura,
então a leitura de teste os grava dentro da imagem, e em execução o worker não depende da internet.
"""

import pytesseract
from PIL import Image, ImageDraw, ImageFont

from . import rapid, tesseract

imagem = Image.new('RGB', (1400, 300), 'white')
ImageDraw.Draw(imagem).text((40, 110), 'PODER JUDICIARIO', fill='black', font=ImageFont.load_default(size=72))
print(f'Tesseract {pytesseract.get_tesseract_version()}; idiomas: {", ".join(pytesseract.get_languages())}')
for motor in (tesseract, rapid):
    leitura = motor(imagem)
    print(f'{leitura.motor}: {leitura.texto!r} (confiança {leitura.confianca}, {leitura.segundos:.2f} s)')
