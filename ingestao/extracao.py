"""Extração do texto dos documentos ingeridos (insumo dos agentes de IA), página a página.

Primeira camada da cascata de extração (ver docs/0013): só o texto que o arquivo já traz, sem
custo. Cada página é classificada para decidir se precisa de OCR:

- COM_TEXTO: texto nativo suficiente e de boa qualidade.
- EM_BRANCO: sem texto e sem imagem grande (folha separadora, verso vazio).
- SEM_TEXTO: sem texto, ou com pouco texto, mas com uma imagem grande: página digitalizada
  (xerox, foto). No eproc, a página escaneada costuma trazer só o carimbo da assinatura em
  texto nativo, por isso "pouco texto + imagem grande" também conta como digitalizada.
- TEXTO_RUIM: há texto, mas com cara de OCR antigo ou de codificação quebrada.

SEM_TEXTO e TEXTO_RUIM precisam de OCR. Os limiares (Limiares) são provisórios e serão
calibrados com as métricas guardadas por página (comando `estatisticas_extracao`, ver docs/0014).

Tipos: PDF (pypdf), HTML (texto visível) e texto puro viram páginas; imagens (fotos, scans)
viram uma página SEM_TEXTO; os demais tipos não têm texto extraível.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class Limiares:
    """Limiares da classificação das páginas. Provisórios: calibrar com as estatísticas de ingestões
    reais (`manage.py estatisticas_extracao`, que também simula outros valores; ver docs/0014)."""

    min_caracteres: int = 20  # abaixo disso (sem contar espaços), a página não tem texto útil
    min_caracteres_com_imagem: int = 300  # imagem grande + menos que isso: digitalizada com carimbo
    imagem_grande_pixels: int = 500_000  # ~700×700 px; logotipos e brasões ficam bem abaixo; scans, acima
    qualidade_minima: float = 0.6  # proporção de palavras plausíveis abaixo da qual o texto é ruim


LIMIARES = Limiares()

TIPOS_HTML = {'text/html', 'application/xhtml+xml'}
TIPOS_IMAGEM = {'image/jpeg', 'image/png', 'image/tiff', 'image/gif', 'image/bmp', 'image/webp'}
# Tags cujo conteúdo não é texto do documento.
TAGS_IGNORADAS = {'script', 'style', 'head', 'title', 'noscript', 'template'}
# Tags que separam blocos de texto.
TAGS_BLOCO = {
    'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure',
    'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre',
    'section', 'table', 'td', 'th', 'tr', 'ul',
}

COM_TEXTO, EM_BRANCO, SEM_TEXTO, TEXTO_RUIM = 'com_texto', 'em_branco', 'sem_texto', 'texto_ruim'
PRECISAM_OCR = (SEM_TEXTO, TEXTO_RUIM)


@dataclass
class Pagina:
    numero: int
    texto: str
    situacao: str
    caracteres: int  # sem contar espaços
    qualidade: float | None  # 0 a 1; None se não há texto para avaliar
    maior_imagem: int  # pixels (largura × altura) da maior imagem da página; 0 se não há


@dataclass
class Extracao:
    paginas: list[Pagina] = field(default_factory=list)
    aviso: str = ''  # por que não há texto, quando não há página (exibido ao usuário)

    @property
    def precisam_ocr(self):
        return [p for p in self.paginas if p.situacao in PRECISAM_OCR]

    @property
    def tem_texto(self):
        """Há texto aproveitável: alguma página COM_TEXTO. O carimbo de uma página escaneada não conta."""
        return any(p.situacao == COM_TEXTO for p in self.paginas)


def extrair_texto(dados: bytes, mimetype: str, encoding: str = '') -> Extracao:
    if mimetype == 'application/pdf':
        return _pdf(dados)
    if mimetype in TIPOS_HTML:
        return Extracao([classificar(1, _HTMLParaTexto.converter(_decodificar(dados, encoding)))])
    if mimetype.startswith('text/'):
        return Extracao([classificar(1, _normalizar(_decodificar(dados, encoding)))])
    if mimetype in TIPOS_IMAGEM:
        # Foto ou scan avulso: o tamanho real não é lido aqui (sem decodificar a imagem); vai para OCR.
        return Extracao([Pagina(1, '', SEM_TEXTO, 0, None, 0)])
    return Extracao(aviso=f'Tipo de arquivo sem extração de texto ({mimetype or "desconhecido"}).')


def classificar(numero, texto, maior_imagem=0) -> Pagina:
    """Classifica uma página pelo texto nativo e pela maior imagem que ela contém."""
    caracteres = len(_ESPACOS.sub('', texto))
    qualidade = qualidade_do_texto(texto) if caracteres else None
    situacao = situacao_da_pagina(caracteres, qualidade, maior_imagem)
    return Pagina(numero, texto, situacao, caracteres, qualidade, maior_imagem)


def situacao_da_pagina(caracteres, qualidade, maior_imagem, limiares=None) -> str:
    """A regra de classificação, só sobre as métricas: permite simular outros limiares sobre as
    métricas já guardadas, sem reprocessar os arquivos. Sem limiares, usa os atuais (LIMIARES)."""
    limiares = limiares or LIMIARES
    imagem_grande = maior_imagem >= limiares.imagem_grande_pixels
    if caracteres < limiares.min_caracteres:
        return SEM_TEXTO if imagem_grande else EM_BRANCO
    if imagem_grande and caracteres < limiares.min_caracteres_com_imagem:
        return SEM_TEXTO
    if qualidade is not None and qualidade < limiares.qualidade_minima:
        return TEXTO_RUIM
    return COM_TEXTO


# Palavra plausível: letras (com acentos), opcionalmente com hífen interno ou pontuação em volta;
# ou um número, data, valor ou referência (12, 1.234,56, 15/03/2024, art. 5º, nº 123).
_PALAVRA = re.compile(r"^[(\[\"'“‘«]*(?:[^\W\d_]+(?:[-'’][^\W\d_]+)*|[\d]+(?:[.,/\-ºª°][\dºª°]*)*[ºª°]?)[)\]\"'”’».,;:!?…]*$")
_ESTRANHOS = re.compile(r'[�-\x00-\x08\x0b\x0c\x0e-\x1f]')  # substituição, uso privado, controle
_ESPACOS = re.compile(r'\s')


def qualidade_do_texto(texto: str) -> float:
    """Proporção (0 a 1) de palavras plausíveis, descontada a de caracteres estranhos.

    Texto bom de documento judicial fica perto de 1; OCR antigo ruim ("C3rt1f1c0 qv3 0 r3u") ou
    texto com codificação quebrada ("ï¿½", caracteres de uso privado) fica bem abaixo.
    """
    palavras = texto.split()
    if not palavras:
        return 0.0
    plausiveis = sum(1 for p in palavras if _PALAVRA.match(p))
    sem_espaco = ''.join(palavras)
    estranhos = len(_ESTRANHOS.findall(sem_espaco)) / len(sem_espaco)
    return round(plausiveis / len(palavras) * (1 - estranhos), 3)


def _pdf(dados):
    try:
        leitor = PdfReader(io.BytesIO(dados))
        paginas = [
            classificar(n, _normalizar(pagina.extract_text() or ''), _maior_imagem(pagina))
            for n, pagina in enumerate(leitor.pages, start=1)
        ]
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning('PDF ilegível: %s', exc)
        return Extracao(aviso='Não foi possível ler o PDF (arquivo protegido ou corrompido).')
    return Extracao(paginas)


def _maior_imagem(objeto, profundidade=2):
    """Pixels da maior imagem de uma página (ou XObject de formulário), lidos só do dicionário
    (/Width × /Height), sem decodificar.

    Desce em XObjects de formulário (/Form), onde alguns geradores embrulham a imagem do scan.
    """
    recursos = objeto.get('/Resources')
    xobjetos = recursos.get_object().get('/XObject') if recursos is not None else None
    if not xobjetos:
        return 0
    maior = 0
    for ref in xobjetos.get_object().values():
        filho = ref.get_object()
        tipo = filho.get('/Subtype')
        if tipo == '/Image':
            maior = max(maior, int(filho.get('/Width', 0)) * int(filho.get('/Height', 0)))
        elif tipo == '/Form' and profundidade > 0:
            maior = max(maior, _maior_imagem(filho, profundidade - 1))
    return maior


def _decodificar(dados, encoding):
    for codificacao in (encoding, 'utf-8'):
        if codificacao:
            try:
                return dados.decode(codificacao)
            except (LookupError, UnicodeDecodeError):
                continue
    return dados.decode('cp1252', errors='replace')  # documentos antigos de tribunais


def _normalizar(texto):
    """Sem espaços sobrando no fim das linhas e no máximo uma linha em branco seguida."""
    linhas = [linha.rstrip() for linha in texto.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(linhas)).strip()


class _HTMLParaTexto(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self.ignorando = 0

    @classmethod
    def converter(cls, html):
        parser = cls()
        parser.feed(html)
        parser.close()
        texto = ''.join(parser.partes)
        # Espaços do código-fonte HTML não são texto: colapsa dentro das linhas.
        linhas = (re.sub(r'[ \t\f\v]+', ' ', linha).strip() for linha in texto.split('\n'))
        return _normalizar('\n'.join(linhas))

    def handle_starttag(self, tag, attrs):
        if tag in TAGS_IGNORADAS:
            self.ignorando += 1
        elif tag in TAGS_BLOCO:
            self.partes.append('\n')

    def handle_endtag(self, tag):
        if tag in TAGS_IGNORADAS:
            self.ignorando = max(0, self.ignorando - 1)
        elif tag in TAGS_BLOCO:
            self.partes.append('\n')

    def handle_data(self, data):
        if not self.ignorando:
            self.partes.append(data.replace('\n', ' '))
