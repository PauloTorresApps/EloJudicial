"""Motores de OCR de verdade. Só rodam onde as dependências estão instaladas (imagem "ocr"):

    docker compose run --rm worker-ocr python manage.py test ocr
"""

import importlib.util
import io
import shutil
import unittest

from django.test import SimpleTestCase

import ocr

TEM_OCR = all(importlib.util.find_spec(m) for m in ('PIL', 'pypdfium2', 'pytesseract', 'rapidocr')) and shutil.which('tesseract')
FRASE = 'PODER JUDICIARIO DO ESTADO DO TOCANTINS'


def pagina_com_texto():
    from PIL import Image, ImageDraw, ImageFont

    imagem = Image.new('RGB', (1700, 400), 'white')
    ImageDraw.Draw(imagem).text((60, 150), FRASE, fill='black', font=ImageFont.load_default(size=56))
    return imagem


@unittest.skipUnless(TEM_OCR, 'dependências de OCR ausentes (só na imagem "ocr")')
class MotoresTests(SimpleTestCase):
    def test_os_dois_motores_leem_uma_pagina_limpa(self):
        imagem = pagina_com_texto()
        for motor in (ocr.tesseract, ocr.rapid):
            with self.subTest(motor=motor.__name__):
                leitura = motor(imagem)
                self.assertEqual(leitura.motor, motor.__name__)
                self.assertIn('TOCANTINS', leitura.texto.upper())
                self.assertGreater(leitura.confianca, 0.5)  # fonte sintética: o Tesseract lê certo, mas com ~0,7

    def test_pagina_em_branco_nao_tem_texto_nem_confianca(self):
        from PIL import Image

        leitura = ocr.tesseract(Image.new('RGB', (800, 600), 'white'))
        self.assertEqual((leitura.texto, leitura.confianca), ('', 0.0))

    def test_paginas_renderiza_so_as_paginas_pedidas_do_pdf(self):
        imagem = pagina_com_texto()
        buffer = io.BytesIO()
        imagem.save(buffer, 'PDF', save_all=True, append_images=[imagem], resolution=150)
        geradas = list(ocr.paginas(buffer.getvalue(), 'application/pdf', [2], dpi=72))
        self.assertEqual([n for n, _ in geradas], [2])
        self.assertEqual(geradas[0][1].mode, 'RGB')

    def test_imagem_avulsa_e_a_pagina_1(self):
        buffer = io.BytesIO()
        pagina_com_texto().save(buffer, 'PNG')
        self.assertEqual([n for n, _ in ocr.paginas(buffer.getvalue(), 'image/png', [1])], [1])


class SemDependenciasTests(SimpleTestCase):
    def test_tipo_sem_ocr_e_recusado(self):
        with self.assertRaises(ValueError):
            list(ocr.paginas(b'texto', 'text/plain', [1]))
