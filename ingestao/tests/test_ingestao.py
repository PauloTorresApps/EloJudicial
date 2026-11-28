"""Ingestão dos documentos marcados na tabela de eventos (lote → fila → original + texto)."""

import dataclasses
import hashlib
import json
import shutil
import tempfile
import zlib
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from ingestao import acesso, extracao
from ingestao.extracao import (
    COM_TEXTO, EM_BRANCO, SEM_TEXTO, TEXTO_RUIM, Extracao, classificar, extrair_texto, qualidade_do_texto,
)
from ingestao.tasks import ingerir_lote, ler_pagina, reconhecer_arquivo, situacao_do_arquivo
from ingestao.models import ArquivoIngerido, LoteIngestao, PaginaExtraida
from judicial import mni
from ocr import Leitura
from judicial.tests.test_processos import NUMERO, RESPOSTA_PROCESSO, Base

Situacao = ArquivoIngerido.Situacao


SCAN = (1240, 1754)  # página A4 digitalizada a 150 dpi
CARIMBO = 'Documento assinado eletronicamente'  # o eproc carimba em texto nativo até as páginas escaneadas


def pdf(*paginas):
    """PDF mínimo. Cada página é um texto (ASCII) ou (texto, (largura, altura) de uma imagem).

    A imagem é só um XObject em branco com as dimensões dadas, como o de uma página digitalizada.
    pdf() é uma página em branco; pdf('x') uma página com o texto x.
    """
    paginas = [p if isinstance(p, tuple) else (p, None) for p in (paginas or ('',))]
    objetos = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']  # 1: catálogo, 2: páginas
    kids = []
    for texto, imagem in paginas:
        conteudo = f'BT /F1 12 Tf 72 720 Td ({texto}) Tj ET'.encode() if texto else b''
        recursos = b'/Font << /F1 3 0 R >>'
        if imagem:
            largura, altura = imagem
            dados = zlib.compress(bytes((largura + 7) // 8 * altura))
            objetos.append(b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray '
                           b'/BitsPerComponent 1 /Filter /FlateDecode /Length %d >>\nstream\n' % (largura, altura, len(dados))
                           + dados + b'\nendstream')
            recursos += b' /XObject << /Im1 %d 0 R >>' % len(objetos)
            conteudo = b'q 612 0 0 792 0 0 cm /Im1 Do Q ' + conteudo
        objetos.append(b'<< /Length %d >>\nstream\n' % len(conteudo) + conteudo + b'\nendstream')
        objetos.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << %s >> >>'
                       % (len(objetos), recursos))
        kids.append(b'%d 0 R' % len(objetos))
    objetos[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objetos[1] = b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(kids), len(kids))
    saida, posicoes = b'%PDF-1.4\n', []
    for n, objeto in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += b'%d 0 obj\n' % n + objeto + b'\nendobj\n'
    xref = len(saida)
    saida += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objetos) + 1)
    saida += b''.join(b'%010d 00000 n \n' % p for p in posicoes)
    return saida + b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objetos) + 1, xref)


TEXTO = 'Sentenca de teste: julgo procedente o pedido.'
HTML = '<html><head><title>x</title><script>alert(1)</script></head><body><p>Certifico que   o réu</p><p>foi intimado.</p></body></html>'


def resposta(id_documento, dados, mimetype):
    return mni.ResultadoDocumentos(sucesso=True, documentos=[mni.ConteudoDocumento(id=id_documento, mimetype=mimetype, dados=dados)])


def tribunal_lote(ids_documentos, **_):
    """consultar_documentos simulado: os documentos de tribunal() numa única resposta."""
    return mni.ResultadoDocumentos(sucesso=True, documentos=[d for i in ids_documentos for d in tribunal(i).documentos])


def tribunal(id_documento, **_):
    """consultar_documento simulado (ids da tabela de exemplo): d1 é um PDF com texto, d1a um PDF misto
    (texto, página escaneada com carimbo, página em branco), d2 um PDF digitalizado e d3 um HTML."""
    return {
        'd1': resposta('d1', pdf(TEXTO), 'application/pdf'),
        'd1a': resposta('d1a', pdf(TEXTO, (CARIMBO, SCAN), ''), 'application/pdf'),
        'd2': resposta('d2', pdf((CARIMBO, SCAN)), 'application/pdf'),
        'd3': resposta('d3', HTML.encode(), 'text/html; charset=utf-8'),
    }[id_documento]


class ArmazenamentoTemporario:
    """Arquivos numa pasta temporária. O FileField resolve o storage ao carregar o modelo, então
    override_settings(STORAGES=...) não teria efeito: troca-se o storage do próprio campo."""

    def setUp(self):
        super().setUp()
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        troca = mock.patch.object(ArquivoIngerido._meta.get_field('arquivo'), 'storage', FileSystemStorage(location=pasta))
        troca.start()
        self.addCleanup(troca.stop)


class IngestaoTests(ArmazenamentoTemporario, Base):
    def exibir_processo(self):
        """Abre a tabela de eventos (guarda os documentos exibidos, como na tela real)."""
        return self.dados_do_processo(mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO))

    def ingerir(self, ids, consultar=tribunal_lote):
        url = reverse('ingestao:ingerir', args=[self.eproc.pk, self.amb1.pk, NUMERO])
        with mock.patch.object(mni, 'consultar_documentos', side_effect=lambda amb, login, senha, numero, ids_documentos:
                               consultar(list(ids_documentos), login=login, senha=senha)) as consultar_documentos, \
                self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {'documentos': ids})
        return resp, consultar_documentos

    def test_tabela_de_eventos_tem_o_formulario_e_o_botao(self):
        resp = self.exibir_processo()
        self.assertContains(resp, f'action="{reverse("ingestao:ingerir", args=[self.eproc.pk, self.amb1.pk, NUMERO])}"')
        self.assertContains(resp, 'form="form-ingestao"')

    def test_ingere_os_marcados_guarda_o_original_e_extrai_o_texto(self):
        self.exibir_processo()
        resp, consultar = self.ingerir(['d1', 'd3', 'd1', 'inexistente'])
        lote = LoteIngestao.objects.get()
        self.assertRedirects(resp, reverse('ingestao:lote', args=[lote.pk]))
        self.assertEqual((lote.usuario, lote.ambiente, lote.numero_processo, lote.login), (self.user, self.amb1, NUMERO, '123'))
        # Sem repetição e só ids que estavam na tabela.
        self.assertEqual(list(lote.arquivos.values_list('id_documento', flat=True)), ['d1', 'd3'])
        # Uma única requisição ao tribunal para o lote, com os ids na ordem da tabela.
        # A senha (hash) chega ao tribunal, mas o job só levou os ids.
        self.assertEqual(consultar.call_count, 1)
        self.assertEqual(consultar.call_args.args[4], ['d1', 'd3'])
        self.assertEqual(consultar.call_args.args[2], hashlib.sha256(self.SENHA.encode()).hexdigest())

        inicial, certidao = lote.arquivos.all()
        self.assertEqual((inicial.nome, inicial.descricao, inicial.tipo_local), ('INIC1', 'PETIÇÃO INICIAL', '58'))
        self.assertEqual((inicial.evento, certidao.evento), (1, 3))
        self.assertEqual((inicial.situacao, inicial.mimetype, inicial.mensagem), (Situacao.PRONTO, 'application/pdf', ''))
        self.assertEqual(inicial.texto, TEXTO)
        self.assertEqual(list(inicial.paginas.values_list('numero', 'situacao')), [(1, 'com_texto')])
        self.assertEqual(inicial.sha256, hashlib.sha256(pdf(TEXTO)).hexdigest())
        self.assertTrue(inicial.arquivo.name.endswith(f'{self.user.pk}/{lote.pk}/{inicial.pk}.pdf'))
        with inicial.arquivo.open('rb') as f:
            self.assertEqual(f.read(), pdf(TEXTO))
        # HTML: sem script/título, espaços colapsados, parágrafos separados por uma linha em branco.
        self.assertEqual((certidao.situacao, certidao.mimetype), (Situacao.PRONTO, 'text/html'))
        self.assertEqual(certidao.texto, 'Certifico que o réu\n\nfoi intimado.')

    def test_pdf_digitalizado_fica_sem_texto_mesmo_com_o_carimbo_da_assinatura(self):
        self.exibir_processo()
        self.ingerir(['d2'])
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual(arquivo.situacao, Situacao.SEM_TEXTO)
        self.assertEqual(arquivo.mensagem, 'Sem texto aproveitável: precisa de OCR (página 1).')
        pagina = arquivo.paginas.get()
        self.assertEqual((pagina.situacao, pagina.texto, pagina.maior_imagem), ('sem_texto', CARIMBO, 1240 * 1754))
        self.assertTrue(arquivo.arquivo)

    def test_pdf_misto_nao_perde_as_paginas_escaneadas_em_silencio(self):
        self.exibir_processo()
        self.ingerir(['d1a'])
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual(arquivo.evento, 1)  # o vinculado fica com o evento do documento principal
        self.assertEqual(arquivo.situacao, Situacao.PARCIAL)
        self.assertEqual(arquivo.mensagem, '1 de 3 páginas precisam de OCR (página 2).')
        self.assertEqual(list(arquivo.paginas.values_list('numero', 'situacao')),
                         [(1, 'com_texto'), (2, 'sem_texto'), (3, 'em_branco')])
        resp = self.client.get(reverse('ingestao:lote', args=[arquivo.lote_id]))
        self.assertContains(resp, '3 páginas (1 para OCR)')
        self.assertContains(resp, 'Texto parcial')
        self.assertContains(resp, '1 com texto parcial')

    def test_sem_a_lista_exibida_nao_cria_lote(self):
        self.informar_senha()  # a tabela nunca foi exibida (ou o cache expirou)
        resp, consultar = self.ingerir(['d1'])
        self.assertRedirects(resp, self.url_processo(self.amb1), fetch_redirect_response=False)
        self.assertFalse(LoteIngestao.objects.exists())
        consultar.assert_not_called()

    def test_sem_senha_nao_cria_lote(self):
        self.exibir_processo()
        self.client.post(reverse('sistemas:esquecer_senha', args=[self.eproc.pk]))
        self.ingerir(['d1'])
        self.assertFalse(LoteIngestao.objects.exists())

    def test_senha_expirada_no_meio_do_lote_aguarda_e_retoma(self):
        self.exibir_processo()
        with mock.patch.object(acesso, 'obter', return_value=None):
            self.ingerir(['d1'])
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual(arquivo.situacao, Situacao.AGUARDANDO_SENHA)

        with mock.patch.object(mni, 'consultar_documentos', side_effect=lambda *a: tribunal_lote(a[4])), \
                self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('ingestao:retomar', args=[arquivo.lote_id]))
        arquivo.refresh_from_db()
        self.assertEqual(arquivo.situacao, Situacao.PRONTO)

    def test_senha_recusada_para_o_lote_e_descarta_o_acesso(self):
        self.exibir_processo()
        recusa = mni.ResultadoDocumentos(sucesso=False, mensagens=['Falha na autenticação: senha inválida'])
        self.ingerir(['d1', 'd3'], consultar=lambda *a, **k: recusa)
        lote = LoteIngestao.objects.get()
        self.assertEqual(set(lote.arquivos.values_list('situacao', flat=True)), {Situacao.AGUARDANDO_SENHA})
        self.assertIsNone(acesso.obter(lote))

    def test_falha_de_comunicacao_tenta_de_novo_e_depois_registra_o_erro(self):
        self.exibir_processo()

        def fora_do_ar(*a, **k):
            raise mni.ErroMNI('Não foi possível conectar ao tribunal.')

        _, consultar = self.ingerir(['d1'], consultar=fora_do_ar)
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual(consultar.call_count, 4)  # 1 + 3 novas tentativas
        self.assertEqual((arquivo.situacao, arquivo.mensagem), (Situacao.ERRO, 'Não foi possível conectar ao tribunal.'))

    def test_arquivo_concluido_nao_e_baixado_de_novo(self):
        from ingestao.tasks import ingerir_arquivo, ingerir_lote
        self.exibir_processo()
        _, consultar = self.ingerir(['d1'])
        arquivo = ArquivoIngerido.objects.get()
        with mock.patch.object(mni, 'consultar_documentos') as de_novo:
            ingerir_lote.delay(arquivo.lote_id, [arquivo.pk])  # mensagem entregue duas vezes
            ingerir_arquivo.delay(arquivo.pk)  # mensagem antiga (um arquivo por tarefa)
        de_novo.assert_not_called()

    def test_documento_que_nao_veio_na_resposta_so_falha_ele(self):
        self.exibir_processo()
        # Sem a tolerância de consulta única: d3 não pode receber o conteúdo de d1.
        self.ingerir(['d1', 'd3'], consultar=lambda ids, **k: tribunal_lote(['d1']))
        inicial, certidao = ArquivoIngerido.objects.order_by('pk')
        self.assertEqual(inicial.situacao, Situacao.PRONTO)
        self.assertEqual((certidao.situacao, certidao.mensagem), (Situacao.ERRO, 'O tribunal não enviou o conteúdo.'))
        self.assertFalse(certidao.arquivo)

    def test_resposta_grande_demais_divide_o_lote_ao_meio(self):
        self.exibir_processo()

        def limitado(ids, **_):
            if len(ids) > 1:
                raise mni.RespostaGrandeDemais('A resposta do tribunal excedeu o tamanho máximo permitido.')
            return tribunal_lote(ids)

        _, consultar = self.ingerir(['d1', 'd1a', 'd3'], consultar=limitado)
        # [d1, d1a, d3] → [d1] e [d1a, d3] → [d1a] e [d3]
        self.assertEqual([c.args[4] for c in consultar.call_args_list],
                         [['d1', 'd1a', 'd3'], ['d1'], ['d1a', 'd3'], ['d1a'], ['d3']])
        self.assertEqual(list(ArquivoIngerido.objects.order_by('pk').values_list('situacao', flat=True)),
                         [Situacao.PRONTO, Situacao.PARCIAL, Situacao.PRONTO])

    def test_senha_recusada_no_lote_dividido_nao_faz_novos_pedidos(self):
        self.exibir_processo()
        recusa = mni.ResultadoDocumentos(sucesso=False, mensagens=['Falha na autenticação: senha inválida'])

        def grande_e_depois_recusa(ids, **_):
            if len(ids) > 1:
                raise mni.RespostaGrandeDemais('grande')
            return recusa

        _, consultar = self.ingerir(['d1', 'd3'], consultar=grande_e_depois_recusa)
        self.assertEqual([c.args[4] for c in consultar.call_args_list], [['d1', 'd3'], ['d1']])  # o d3 não é pedido
        self.assertEqual(set(ArquivoIngerido.objects.values_list('situacao', flat=True)), {Situacao.AGUARDANDO_SENHA})

    def test_falha_no_meio_do_lote_dividido_so_repete_o_que_faltou(self):
        self.exibir_processo()
        tentativas = []

        def instavel(ids, **_):
            tentativas.append(ids)
            if len(ids) > 1:
                raise mni.RespostaGrandeDemais('grande')
            if ids == ['d3'] and len(tentativas) == 3:
                raise mni.ErroMNI('Não foi possível conectar ao tribunal.')
            return tribunal_lote(ids)

        self.ingerir(['d1', 'd3'], consultar=instavel)
        self.assertEqual(tentativas, [['d1', 'd3'], ['d1'], ['d3'], ['d3']])  # a nova tentativa leva só o d3
        self.assertEqual(set(ArquivoIngerido.objects.values_list('situacao', flat=True)), {Situacao.PRONTO})

    def test_paginas_do_lote_original_e_texto(self):
        self.exibir_processo()
        self.ingerir(['d1'])
        arquivo = ArquivoIngerido.objects.get()
        resp = self.client.get(reverse('ingestao:lote', args=[arquivo.lote_id]))
        self.assertContains(resp, 'INIC1')
        self.assertContains(resp, '1/1 concluído')
        self.assertNotContains(resp, 'hx-trigger="every 3s"')  # terminou: não consulta mais o andamento
        self.assertContains(self.client.get(reverse('ingestao:lotes')), f'#{arquivo.lote_id}')
        original = self.client.get(reverse('ingestao:original', args=[arquivo.pk]))
        self.assertEqual((original['Content-Type'], original.content), ('application/pdf', pdf(TEXTO)))
        texto = self.client.get(reverse('ingestao:texto', args=[arquivo.pk]))
        self.assertEqual(texto.content.decode(), TEXTO)

    def test_andamento_consulta_ate_terminar(self):
        self.exibir_processo()
        with mock.patch('ingestao.tasks.ingerir_lote.delay'):  # fica na fila
            self.ingerir(['d1'])
        lote = LoteIngestao.objects.get()
        url = reverse('ingestao:andamento', args=[lote.pk])
        resp = self.client.get(url)
        self.assertContains(resp, 'hx-trigger="every 3s"')
        self.assertNotIn('HX-Refresh', resp)
        lote.arquivos.update(situacao=Situacao.PRONTO)
        self.assertEqual(self.client.get(url)['HX-Refresh'], 'true')

    def test_lotes_e_arquivos_sao_so_do_dono(self):
        self.exibir_processo()
        self.ingerir(['d1'])
        arquivo = ArquivoIngerido.objects.get()
        beto = get_user_model().objects.create_user('beto', email='beto@exemplo.com', password='x')
        self.client.force_login(beto)
        for nome, pk in (('lote', arquivo.lote_id), ('andamento', arquivo.lote_id), ('original', arquivo.pk), ('texto', arquivo.pk)):
            self.assertEqual(self.client.get(reverse(f'ingestao:{nome}', args=[pk])).status_code, 404, nome)
        self.assertEqual(self.client.post(reverse('ingestao:retomar', args=[arquivo.lote_id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('ingestao:lotes')), f'#{arquivo.lote_id}')

    def test_apagar_o_lote_apaga_os_arquivos(self):
        self.exibir_processo()
        self.ingerir(['d1'])
        arquivo = ArquivoIngerido.objects.get()
        armazenamento, nome = arquivo.arquivo.storage, arquivo.arquivo.name
        self.assertTrue(armazenamento.exists(nome))
        arquivo.lote.delete()
        self.assertFalse(armazenamento.exists(nome))

    def test_sair_descarta_o_acesso_dos_lotes_em_andamento(self):
        self.exibir_processo()
        with mock.patch('ingestao.tasks.ingerir_lote.delay'):
            self.ingerir(['d1'])
        lote = LoteIngestao.objects.get()
        self.assertIsNotNone(acesso.obter(lote))
        self.client.post(reverse('logout'))
        self.assertIsNone(acesso.obter(lote))


class MedicaoTests(ArmazenamentoTemporario, Base):
    """Comandos para calibrar os limiares com ingestões reais (docs/0014)."""

    def setUp(self):
        super().setUp()
        self.dados_do_processo(mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO))
        url = reverse('ingestao:ingerir', args=[self.eproc.pk, self.amb1.pk, NUMERO])
        with mock.patch.object(mni, 'consultar_documentos', side_effect=lambda *a: tribunal_lote(a[4])), \
                self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'documentos': ['d1', 'd1a', 'd2', 'd3']})

    def estatisticas(self, *args):
        saida = StringIO()
        call_command('estatisticas_extracao', *args, stdout=saida)
        return saida.getvalue()

    def test_estatisticas_so_com_contagens(self):
        r = json.loads(self.estatisticas('--json'))
        self.assertEqual(r['arquivos_por_situacao'], {'pronto': 2, 'parcial': 1, 'sem_texto': 1})
        # d1 e d3: uma página com texto cada; d1a: texto, escaneada e em branco; d2: escaneada.
        self.assertEqual(r['paginas_por_situacao'], {'com_texto': 3, 'sem_texto': 2, 'em_branco': 1})
        self.assertEqual(r['paginas_por_sistema'], {'EPROC TJTO': {'paginas': 6, 'precisam_ocr': 2}})
        self.assertEqual(r['caracteres_por_pagina']['20–99'], {'sem_imagem_grande': 3, 'com_imagem_grande': 2})
        self.assertNotIn('simulacao', r)
        texto = self.estatisticas()
        self.assertIn('Páginas com o texto nativo: 6', texto)
        self.assertNotIn('Sentenca', texto)  # nenhum conteúdo de documento no relatório
        self.assertEqual(self.estatisticas('--json', '--sistema', 'PJE TJBA').count('"pronto"'), 0)

    def test_simulacao_de_outros_limiares(self):
        r = json.loads(self.estatisticas('--json', '--simular', 'min_caracteres_com_imagem=10'))
        self.assertEqual(r['simulacao']['mudancas'], {'sem_texto -> com_texto': 2})
        self.assertEqual((r['simulacao']['precisam_ocr_atual'], r['simulacao']['precisam_ocr_simulado']), (2, 0))
        with self.assertRaisesMessage(CommandError, 'Limiar inválido'):
            self.estatisticas('--simular', 'limiar_que_nao_existe=1')

    def test_reextrair_aplica_novos_limiares_sem_consultar_o_tribunal(self):
        novos = dataclasses.replace(extracao.LIMIARES, min_caracteres_com_imagem=10)
        saida = StringIO()
        with mock.patch.object(extracao, 'LIMIARES', novos), mock.patch.object(mni, 'consultar_documentos') as tribunal_:
            call_command('reextrair', '--todos', stdout=saida)
        tribunal_.assert_not_called()
        self.assertIn('4 arquivo(s) reprocessado(s); 2 mudaram de situação.', saida.getvalue())
        self.assertEqual(set(ArquivoIngerido.objects.values_list('situacao', flat=True)), {Situacao.PRONTO})
        self.assertEqual(PaginaExtraida.objects.filter(situacao='sem_texto').count(), 0)


class ExtracaoTests(SimpleTestCase):
    def situacoes(self, extracao):
        return [p.situacao for p in extracao.paginas]

    def test_classificacao_de_cada_pagina(self):
        logo = 200 * 200  # brasão no cabeçalho: imagem pequena
        casos = [
            ('', 0, EM_BRANCO),
            ('12', logo, EM_BRANCO),  # só o número da página
            ('', SCAN[0] * SCAN[1], SEM_TEXTO),
            (CARIMBO, SCAN[0] * SCAN[1], SEM_TEXTO),  # escaneada com o carimbo da assinatura
            (TEXTO * 10, SCAN[0] * SCAN[1], COM_TEXTO),  # texto de verdade com uma imagem grande
            (TEXTO, logo, COM_TEXTO),
            ('C3rt1f1c0 qv3 0 r3u f01 1nt1m4d0 n3st4 d4t4 p4r4 0 4t0', 0, TEXTO_RUIM),  # OCR antigo ruim
        ]
        for texto, imagem, esperada in casos:
            with self.subTest(texto=texto, imagem=imagem):
                self.assertEqual(classificar(1, texto, imagem).situacao, esperada)

    def test_qualidade_do_texto(self):
        bom = 'Vistos. Trata-se de ação de cobrança (art. 5º, II) no valor de R$ 1.234,56, ajuizada em 15/03/2024.'
        self.assertGreater(qualidade_do_texto(bom), 0.9)
        self.assertLess(qualidade_do_texto('C3rt1f1c0 qv3 0 r3u f01 1nt1m4d0'), 0.6)
        self.assertLess(qualidade_do_texto('Certid\ufffdo de intima\ufffd\ufffdo do r\ufffdu \ue001\ue002\ue003'), 0.6)
        self.assertEqual(qualidade_do_texto(''), 0.0)

    def test_pdf_pagina_a_pagina(self):
        extracao = extrair_texto(pdf(TEXTO, (CARIMBO, SCAN), ''), 'application/pdf')
        self.assertEqual(self.situacoes(extracao), [COM_TEXTO, SEM_TEXTO, EM_BRANCO])
        self.assertEqual([p.maior_imagem for p in extracao.paginas], [0, SCAN[0] * SCAN[1], 0])
        self.assertEqual([p.numero for p in extracao.precisam_ocr], [2])

    def test_imagem_avulsa_vai_para_ocr(self):
        extracao = extrair_texto(b'\xff\xd8\xff...', 'image/jpeg')
        self.assertEqual(self.situacoes(extracao), [SEM_TEXTO])

    def test_texto_puro_em_cp1252_e_tipos_sem_texto(self):
        extracao = extrair_texto('Decisão proferida nos autos\r\n\r\n\r\nfim  '.encode('cp1252'), 'text/plain')
        self.assertEqual(extracao.paginas[0].texto, 'Decisão proferida nos autos\n\nfim')
        extracao = extrair_texto(b'ID3...', 'audio/mpeg')
        self.assertEqual(extracao.paginas, [])
        self.assertIn('audio/mpeg', extracao.aviso)

    def test_pdf_corrompido_nao_quebra(self):
        with self.assertLogs('pypdf', level='WARNING'):  # o pypdf avisa do arquivo truncado
            extracao = extrair_texto(b'%PDF-1.4 lixo', 'application/pdf')
        self.assertEqual(extracao.paginas, [])
        self.assertIn('Não foi possível ler o PDF', extracao.aviso)

    def test_situacao_e_mensagem_do_arquivo(self):
        def situacao(*paginas):
            return situacao_do_arquivo(Extracao([classificar(n, t, i) for n, (t, i) in enumerate(paginas, start=1)]))

        escaneada, com_texto, branca = ('', SCAN[0] * SCAN[1]), (TEXTO, 0), ('', 0)
        self.assertEqual(situacao(com_texto, branca), (Situacao.PRONTO, ''))
        self.assertEqual(situacao(branca), (Situacao.SEM_TEXTO, 'O documento está em branco.'))
        self.assertEqual(situacao(escaneada, escaneada), (Situacao.SEM_TEXTO, 'Sem texto aproveitável: precisa de OCR (páginas 1–2).'))
        self.assertEqual(
            situacao(escaneada, escaneada, com_texto, escaneada, com_texto, escaneada, escaneada, escaneada),
            (Situacao.PARCIAL, '6 de 8 páginas precisam de OCR (páginas 1–2, 4, 6–8).'),
        )
        paginas = Extracao([classificar(1, *escaneada), classificar(2, *com_texto)])
        self.assertEqual(situacao_do_arquivo(paginas, vai_para_ocr=True),
                         (Situacao.RECONHECENDO, '1 de 2 páginas na fila do OCR (página 1).'))
        paginas = Extracao([classificar(1, *com_texto)])
        self.assertEqual(situacao_do_arquivo(paginas, vai_para_ocr=True), (Situacao.PRONTO, ''))


BOM = 'Certifico que o réu foi intimado pessoalmente nesta data, conforme o mandado juntado aos autos.'
RUIM = '@#% ~~ |||| ^^ ;;; ,,, }{ ¨¨ ## %% && ** ++ == << >> ´´'


@override_settings(INGESTAO_OCR=True)
class OcrTests(ArmazenamentoTemporario, Base):
    """Orquestração do OCR (docs/0018 e 0019), com os motores simulados: eles só existem na imagem "ocr"."""

    exibir_processo = IngestaoTests.exibir_processo
    ingerir = IngestaoTests.ingerir

    def setUp(self):
        super().setUp()
        self.tesseract = self.simular('tesseract', Leitura(BOM, 0.93, 'tesseract', 0.1))
        self.rapid = self.simular('rapid', Leitura(BOM, 0.99, 'rapid', 0.1))
        self.simular('paginas', side_effect=lambda dados, mimetype, numeros: ((n, f'imagem {n}') for n in numeros))
        self.exibir_processo()

    def simular(self, nome, retorno=None, **kwargs):
        patcher = mock.patch(f'ocr.{nome}', return_value=retorno, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_pagina_escaneada_ganha_o_texto_do_tesseract(self):
        self.ingerir(['d2'])
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual((arquivo.situacao, arquivo.mensagem), (Situacao.PRONTO, ''))
        pagina = arquivo.paginas.get()
        self.assertEqual((pagina.texto, pagina.metodo, pagina.confianca, pagina.situacao),
                         (BOM, 'ocr_tesseract', 0.93, 'com_texto'))
        self.assertEqual(pagina.maior_imagem, SCAN[0] * SCAN[1])  # a métrica da imagem fica
        self.rapid.assert_not_called()

    def test_so_as_paginas_que_precisam_vao_para_o_ocr(self):
        self.ingerir(['d1', 'd1a'])
        self.assertEqual(self.tesseract.call_count, 1)  # d1 tem texto; no d1a, só a página 2 é escaneada
        misto = ArquivoIngerido.objects.get(id_documento='d1a')
        self.assertEqual(list(misto.paginas.values_list('numero', 'metodo')),
                         [(1, 'nativo'), (2, 'ocr_tesseract'), (3, 'nativo')])
        self.assertEqual(misto.situacao, Situacao.PRONTO)

    def test_confianca_baixa_tenta_o_rapidocr_e_fica_a_melhor_leitura(self):
        self.tesseract.return_value = Leitura(RUIM, 0.5, 'tesseract', 0.1)
        self.ingerir(['d2'])
        pagina = PaginaExtraida.objects.get()
        self.assertEqual((pagina.texto, pagina.metodo, pagina.confianca), (BOM, 'ocr_rapid', 0.99))
        # As duas leituras ficam guardadas (sem o texto), também a do Tesseract, que perdeu.
        self.assertEqual([(lida['motor'], lida['confianca']) for lida in pagina.leituras], [('tesseract', 0.5), ('rapid', 0.99)])
        self.assertLess(pagina.leituras[0]['qualidade'], pagina.leituras[1]['qualidade'])

    def test_estatisticas_do_ocr_para_calibrar_os_limiares(self):
        self.tesseract.return_value = Leitura(RUIM, 0.6, 'tesseract', 1.5)
        self.rapid.return_value = Leitura(BOM, 0.99, 'rapid', 4.5)
        self.ingerir(['d1a', 'd2'])
        self.tesseract.return_value = Leitura(BOM, 0.93, 'tesseract', 0.5)
        self.ingerir(['d2'])  # mesmo documento em outro lote: reaproveitado, e conta uma vez só
        saida = StringIO()
        call_command('estatisticas_extracao', '--json', stdout=saida)
        r = json.loads(saida.getvalue())
        self.assertEqual(r['paginas_repetidas'], 1)
        self.assertEqual(r['ocr']['paginas_por_metodo_e_situacao'], {'ocr_rapid / com_texto': 2})
        self.assertEqual(r['ocr']['confianca_do_tesseract']['0.5–0.7'],
                         {'paginas': 2, 'segunda_leitura': 2, 'rapid_venceu': 2})
        self.assertEqual(r['ocr']['segundos_por_leitura'], {'rapid': 4.5, 'tesseract': 1.5})
        self.assertEqual(r['ocr']['paginas_sem_leituras'], 0)
        saida = StringIO()
        call_command('estatisticas_extracao', stdout=saida)
        self.assertIn('Confiança do Tesseract (abaixo de 0.85', saida.getvalue())
        self.assertNotIn('Certifico', saida.getvalue())  # nenhum texto de documento

    def test_a_leitura_de_melhor_qualidade_vence_mesmo_com_confianca_menor(self):
        self.tesseract.return_value = Leitura(BOM, 0.6, 'tesseract', 0.1)
        self.rapid.return_value = Leitura(RUIM, 0.97, 'rapid', 0.1)
        self.assertEqual(ler_pagina('imagem')[0].motor, 'tesseract')

    def test_ilegivel_mesmo_com_ocr(self):
        self.tesseract.return_value = Leitura(RUIM, 0.4, 'tesseract', 0.1)
        self.rapid.return_value = Leitura(RUIM, 0.95, 'rapid', 0.1)
        self.ingerir(['d2'])
        arquivo = ArquivoIngerido.objects.get()
        self.assertEqual((arquivo.situacao, arquivo.mensagem),
                         (Situacao.SEM_TEXTO, 'Sem texto aproveitável mesmo com OCR (página 1).'))
        self.assertEqual(arquivo.paginas.get().situacao, 'ilegivel')  # não volta ao OCR; fica para o multimodal
        reconhecer_arquivo(arquivo.pk)
        self.assertEqual(self.tesseract.call_count, 1)
        resp = self.client.get(reverse('ingestao:lote', args=[arquivo.lote_id]))
        self.assertNotContains(resp, 'para OCR')

    def test_idempotente_e_reaproveita_o_mesmo_documento_de_outro_lote(self):
        self.ingerir(['d2'])
        primeiro = ArquivoIngerido.objects.get()
        reconhecer_arquivo(primeiro.pk)  # mensagem repetida: nada a fazer
        self.ingerir(['d2'])  # mesmo conteúdo (SHA-256), outro lote
        self.assertEqual(self.tesseract.call_count, 1)
        segundo = ArquivoIngerido.objects.exclude(pk=primeiro.pk).get()
        self.assertEqual((segundo.situacao, segundo.paginas.get().texto), (Situacao.PRONTO, BOM))

    def test_reextrair_devolve_as_paginas_ao_ocr(self):
        self.ingerir(['d2'])
        with self.captureOnCommitCallbacks(execute=True):
            call_command('reextrair', '--todos', stdout=StringIO())
        self.assertEqual(self.tesseract.call_count, 2)
        self.assertEqual(PaginaExtraida.objects.get().metodo, 'ocr_tesseract')

    def test_reconhecendo_enquanto_o_ocr_roda(self):
        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay') as delay:  # fica na fila do OCR
            self.ingerir(['d1', 'd2'])
        escaneado = ArquivoIngerido.objects.get(id_documento='d2')
        delay.assert_called_once_with(escaneado.pk, True)
        self.assertEqual((escaneado.situacao, escaneado.mensagem),
                         (Situacao.RECONHECENDO, '1 de 1 páginas na fila do OCR (página 1).'))
        self.assertFalse(escaneado.tem_texto)  # o texto só aparece com o OCR concluído

        lote = escaneado.lote
        andamento = self.client.get(reverse('ingestao:andamento', args=[lote.pk]))
        self.assertContains(andamento, 'hx-trigger="every 3s"')  # a tela continua acompanhando
        self.assertContains(andamento, '1/2 concluídos')
        self.assertContains(andamento, '1 reconhecendo texto')
        self.assertNotContains(andamento, 'na fila</span>')  # não está mais na fila de download
        self.assertNotIn('HX-Refresh', andamento)
        self.assertContains(self.client.get(reverse('ingestao:lotes')), 'reconhecendo texto')

        with mock.patch('ingestao.tasks.mni.consultar_documentos') as consultar:  # mensagem repetida do download
            ingerir_lote(lote.pk, [escaneado.pk])
        consultar.assert_not_called()

        reconhecer_arquivo(escaneado.pk)
        escaneado.refresh_from_db()
        self.assertEqual(escaneado.situacao, Situacao.PRONTO)
        self.assertEqual(self.client.get(reverse('ingestao:andamento', args=[lote.pk]))['HX-Refresh'], 'true')

    def escaneado_de_tres_paginas(self):
        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay'):
            self.ingerir(['d2'], consultar=lambda ids, **_: resposta('d2', pdf(*[(CARIMBO, SCAN)] * 3), 'application/pdf'))
        return ArquivoIngerido.objects.latest('pk')

    @mock.patch('ingestao.tasks.PAGINAS_POR_TAREFA', 2)
    def test_arquivo_grande_e_lido_em_varias_tarefas(self):
        arquivo = self.escaneado_de_tres_paginas()
        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay') as delay:
            reconhecer_arquivo(arquivo.pk)
        self.assertEqual(self.tesseract.call_count, 2)
        delay.assert_called_once_with(arquivo.pk, True)  # o resto vai para o fim da fila
        arquivo.refresh_from_db()
        self.assertEqual((arquivo.situacao, arquivo.mensagem),
                         (Situacao.RECONHECENDO, '1 de 3 páginas na fila do OCR (página 3).'))

        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay') as delay:
            reconhecer_arquivo(arquivo.pk)
        delay.assert_not_called()
        self.assertEqual(self.tesseract.call_count, 3)
        arquivo.refresh_from_db()
        self.assertEqual(arquivo.situacao, Situacao.PRONTO)

    @mock.patch('ingestao.tasks.PAGINAS_POR_TAREFA', 1)
    def test_as_tarefas_encadeadas_leem_o_arquivo_inteiro(self):
        arquivo = self.escaneado_de_tres_paginas()
        reconhecer_arquivo(arquivo.pk)  # modo eager: as continuações rodam na hora
        self.assertEqual(self.tesseract.call_count, 3)
        arquivo.refresh_from_db()
        self.assertEqual(arquivo.situacao, Situacao.PRONTO)
        self.assertEqual(set(arquivo.paginas.values_list('metodo', flat=True)), {'ocr_tesseract'})

    @mock.patch('ingestao.tasks.PAGINAS_POR_TAREFA', 1)
    def test_paginas_reaproveitadas_nao_contam_no_limite(self):
        primeiro = self.escaneado_de_tres_paginas()
        reconhecer_arquivo(primeiro.pk)
        segundo = self.escaneado_de_tres_paginas()  # mesmo SHA-256, outro lote
        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay') as delay:
            reconhecer_arquivo(segundo.pk)
        delay.assert_not_called()  # as 3 páginas vieram do primeiro, numa só tarefa
        self.assertEqual(self.tesseract.call_count, 3)
        segundo.refresh_from_db()
        self.assertEqual(segundo.situacao, Situacao.PRONTO)

    def test_falha_no_ocr_tira_o_arquivo_de_reconhecendo(self):
        self.tesseract.side_effect = [Leitura(BOM, 0.93, 'tesseract', 0.1), RuntimeError('motor quebrou')]
        with mock.patch('ingestao.tasks.reconhecer_arquivo.delay'):  # duas páginas escaneadas
            self.ingerir(['d2'], consultar=lambda ids, **_: resposta('d2', pdf((CARIMBO, SCAN), (CARIMBO, SCAN)), 'application/pdf'))
        arquivo = ArquivoIngerido.objects.get()
        with self.assertRaises(RuntimeError):
            reconhecer_arquivo(arquivo.pk)
        arquivo.refresh_from_db()
        self.assertEqual((arquivo.situacao, arquivo.mensagem),
                         (Situacao.PARCIAL, '1 de 2 páginas precisam de OCR (página 2).'))  # a 1ª foi lida e fica

    def test_reextrair_le_de_novo_em_vez_de_copiar_o_ocr_antigo(self):
        self.ingerir(['d2'])
        self.ingerir(['d2'])  # mesmo documento: o segundo lote copia do primeiro
        self.assertEqual(self.tesseract.call_count, 1)
        self.tesseract.return_value = Leitura(BOM, 0.97, 'tesseract', 0.1)  # ex.: outro motor ou limiar
        with self.captureOnCommitCallbacks(execute=True):
            call_command('reextrair', '--todos', stdout=StringIO())
        self.assertEqual(self.tesseract.call_count, 3)  # os dois lidos de novo
        self.assertEqual(set(PaginaExtraida.objects.values_list('confianca', flat=True)), {0.97})

    @override_settings(INGESTAO_OCR=False)
    def test_desligado_nao_poe_na_fila(self):
        self.ingerir(['d2'])
        self.tesseract.assert_not_called()
        self.assertEqual(ArquivoIngerido.objects.get().situacao, Situacao.SEM_TEXTO)
