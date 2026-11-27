"""Páginas de um arquivo como imagem, para o OCR."""

import io

DPI = 300  # resolução da renderização do PDF (docs/0019: a escolha definitiva ainda está em aberto)


def paginas(dados: bytes, mimetype: str, numeros, dpi: int = DPI):
    """Gera (número, imagem RGB do Pillow) para cada página pedida (1 = primeira).

    PDF: cada página é renderizada a `dpi`. Imagem avulsa (foto, scan): uma única página, a 1.
    """
    if mimetype == 'application/pdf':
        yield from _pdf(dados, numeros, dpi)
    elif mimetype.startswith('image/'):
        if 1 in numeros:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(dados)) as imagem:
                yield 1, ImageOps.exif_transpose(imagem).convert('RGB')  # fotos de celular vêm giradas no EXIF
    else:
        raise ValueError(f'Tipo de arquivo sem OCR: {mimetype or "desconhecido"}')


def _pdf(dados, numeros, dpi):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(dados)
    try:
        for numero in numeros:
            pagina = pdf[numero - 1]
            try:
                yield numero, pagina.render(scale=dpi / 72).to_pil().convert('RGB')
            finally:
                pagina.close()
    finally:
        pdf.close()
