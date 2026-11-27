"""Páginas de um arquivo como imagem, para o OCR."""

import io

DPI = 300  # resolução da renderização do PDF (docs/0019: a escolha definitiva ainda está em aberto)


def paginas(origem, mimetype: str, numeros, dpi: int = DPI):
    """Gera (número, imagem RGB do Pillow) para cada página pedida (1 = primeira).

    `origem`: caminho do arquivo (preferível: o PDF é lido sob demanda) ou o conteúdo em bytes.
    PDF: cada página é renderizada a `dpi`. Imagem avulsa (foto, scan): uma única página, a 1.
    """
    if mimetype == 'application/pdf':
        yield from _pdf(origem, numeros, dpi)
    elif mimetype.startswith('image/'):
        if 1 in numeros:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(origem) if isinstance(origem, bytes) else origem) as imagem:
                yield 1, _rgb(ImageOps.exif_transpose(imagem))  # fotos de celular vêm giradas no EXIF
    else:
        raise ValueError(f'Tipo de arquivo sem OCR: {mimetype or "desconhecido"}')


def _pdf(origem, numeros, dpi):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(origem)
    try:
        for numero in numeros:
            pagina = pdf[numero - 1]
            try:
                yield numero, _rgb(pagina.render(scale=dpi / 72).to_pil())
            finally:
                pagina.close()
    finally:
        pdf.close()


def _rgb(imagem):
    return imagem if imagem.mode == 'RGB' else imagem.convert('RGB')  # convert() sempre copia (~26 MB numa A4)
