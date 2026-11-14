import urllib.error
from hashlib import sha256
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from judicial import mni, wsdl
from judicial.models import SistemaJudicial
from judicial.preferencias import definir_ambiente_padrao

from .test_avisos import RESPOSTA_FALHA_AUTH, WSDL, _Resposta

DADOS = Path(__file__).parent / 'dados'
RESPOSTA_PROCESSO = (DADOS / 'processo_resposta.xml').read_bytes()
NUMERO = '00012341420248272729'
NUMERO_FORMATADO = '0001234-14.2024.8.27.2729'
RESPOSTA_NAO_ENCONTRADO = b'''<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://www.cnj.jus.br/mni/v300/intercomunicacao" xmlns:ns2="http://www.cnj.jus.br/mni/v300/tipos-servico-intercomunicacao" xmlns:ns3="http://www.cnj.jus.br/mni/v300/"><SOAP-ENV:Body><ns3:respostaConsultarProcesso><ns2:recibo><ns1:sucesso>false</ns1:sucesso><ns1:mensagens><ns1:descritivo>Processo n\xc3\xa3o encontrado</ns1:descritivo><ns1:codigo>2</ns1:codigo><ns1:codigoUnico>x</ns1:codigoUnico></ns1:mensagens></ns2:recibo></ns3:respostaConsultarProcesso></SOAP-ENV:Body></SOAP-ENV:Envelope>'''


class NumeroProcessoTests(TestCase):
    def test_aceita_com_e_sem_mascara(self):
        for entrada in (NUMERO, NUMERO_FORMATADO, f'  {NUMERO_FORMATADO} '):
            self.assertEqual(mni.normalizar_numero_processo(entrada), NUMERO)

    def test_recusa_tamanho_errado_e_digito_verificador_invalido(self):
        with self.assertRaisesMessage(mni.NumeroInvalido, '20 dígitos'):
            mni.normalizar_numero_processo('123')
        with self.assertRaisesMessage(mni.NumeroInvalido, 'dígito verificador'):
            mni.normalizar_numero_processo('0001234-15.2024.8.27.2729')

    def test_formatacao(self):
        self.assertEqual(mni.formatar_numero_processo(NUMERO), NUMERO_FORMATADO)


class ClienteConsultarProcessoTests(TestCase):
    def setUp(self):
        cache.clear()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)
        self.ambiente = SistemaJudicial.objects.get(nome='EPROC TJTO').ambientes.order_by('ordem').first()

    def test_requisicao_segue_o_xsd(self):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_PROCESSO)) as urlopen:
            mni.consultar_processo(self.ambiente, '123', 'hash', NUMERO)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header('Soapaction'), '"http://www.cnj.jus.br/mni/v300/consultarProcesso"')
        corpo = req.data.decode()
        ordem = ['consultante', 'numeroProcesso', 'incluirCabecalho', 'incluirPartes', 'incluirEnderecos',
                 'incluirMovimentos', 'incluirDocumentos', 'parametros']
        posicoes = [corpo.index(f':{nome}') for nome in ordem]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn(f'numeroProcesso>{NUMERO}<', corpo)
        self.assertIn('incluirDocumentos>true<', corpo)
        self.assertRegex(corpo, r'<ns\d:parametros nome="incluirDescricoes" valor="true" />')

    def test_interpreta_processo(self):
        r = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO)
        p = r.processo
        self.assertTrue(r.sucesso)
        self.assertEqual(p.numero_formatado, NUMERO_FORMATADO)
        self.assertEqual(p.orgao_julgador, '1ª Vara Cível de Palmas')
        self.assertEqual(p.classe, '7')
        self.assertEqual(p.valor_causa, 15000.5)
        self.assertEqual(p.assuntos, ['Indenização por Dano Moral', 'Responsabilidade Civil, DIREITO CIVIL'])
        self.assertEqual([(pl.nome, [x.nome for x in pl.partes]) for pl in p.polos],
                         [('AUTOR', ['MINISTÉRIO PÚBLICO DO ESTADO DO TOCANTINS']), ('RÉU', ['FULANO DE TAL']),
                          ('Terceiro', ['DELEGACIA DE POLÍCIA DE PALMAS'])])
        self.assertEqual(p.polos[1].partes[0].advogados, ['BELTRANA ADVOGADA'])
        self.assertEqual([m.descricao for m in p.movimentos],
                         ['Expedida/certificada a intimação eletrônica', 'Movimento 11010', 'Distribuído por sorteio'])
        self.assertEqual(p.movimentos[0].complementos, ['Prazo: 15 dias'])

    def test_campos_da_capa_partes_e_eventos(self):
        p = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO).processo
        self.assertEqual((p.competencia, p.outros_numeros, p.prioridades_descricao),
                         ('14', ['2024.0001234-01 DP Palmas'], ['Idoso', 'Cível']))
        self.assertEqual([pl.codigo for pl in p.polos_principais], ['AT', 'PA'])
        self.assertEqual([pl.nome for pl in p.outros_polos], ['Terceiro'])
        mp, reu = p.polos[0].partes[0], p.polos[1].partes[0]
        self.assertEqual((mp.documento_formatado, mp.tipo_pessoa_descricao), ('01.786.078/0001-46', 'Pessoa Jurídica'))
        self.assertEqual((reu.documento_formatado, reu.tipo_pessoa_descricao), ('123.456.789-09', 'Pessoa Física'))
        adv = reu.representantes[0]
        self.assertEqual((adv.nome, adv.inscricao_formatada, adv.tipo_descricao), ('BELTRANA ADVOGADA', 'OAB/TO 1234', 'Advogado(a)'))
        # eventos numerados em ordem cronológica (1 = mais antigo), exibidos do mais recente para o mais antigo
        self.assertEqual([(m.numero, m.total_documentos) for m in p.movimentos], [(3, 1), (2, 0), (1, 2)])

    def test_descricoes_do_eproc_via_outro_parametro(self):
        """Com incluirDescricoes=true o eproc envia desClasse, desAssunto e desTipoParte* como outroParametro."""
        p = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO).processo
        self.assertEqual((p.classe, p.classe_descricao), ('7', 'Procedimento Comum Cível'))
        self.assertEqual(p.situacao, 'MOVIMENTO')
        self.assertEqual(p.data_ajuizamento.strftime('%d/%m/%Y %H:%M:%S'), '15/03/2024 14:22:33')  # dataAutuacao
        self.assertEqual([pl.nome for pl in p.polos_principais], ['AUTOR', 'RÉU'])
        # parâmetros já exibidos em campos próprios não se repetem em "Informações adicionais"
        self.assertEqual(p.informacoes_adicionais, [('Rito processual', 'Ordinário'), ('Antecipação de tutela', 'Não Requerida')])

    def test_sem_descricoes_usa_codigos_e_nomes_padrao(self):
        import re as _re
        conteudo = _re.sub(rb'<ns1:outroParametro nome="(desClasse|desAssunto|desTipoParte..)"[^>]*/>', b'', RESPOSTA_PROCESSO)
        p = mni.ClienteMNI300.interpretar_processo(conteudo).processo
        self.assertEqual(p.classe_descricao, '')
        self.assertIn('Assunto 7780', p.assuntos)
        self.assertEqual([pl.nome for pl in p.polos_principais], ['Polo ativo', 'Polo passivo'])

    def test_documentos_relacionados_aos_eventos(self):
        p = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO).processo
        evento = {m.numero: m for m in p.movimentos}
        # evento 1: pelo idDocumentoVinculado do movimento
        self.assertEqual([d.nome for d in evento[1].documentos], ['INIC1', 'ANEXO2'])
        inicial = evento[1].documentos[0]
        self.assertEqual((inicial.descricao, inicial.tamanho_formatado, [v.nome for v in inicial.vinculados]),
                         ('PETIÇÃO INICIAL', '43 KB', ['PROC1']))
        certidao = evento[3].documentos[0]
        self.assertEqual((certidao.mimetype, certidao.tamanho_formatado), ('text/html', '2.0 MB'))
        # evento 3: pelo idMovimento do documento
        self.assertEqual([(d.nome, d.nivel_sigilo) for d in evento[3].documentos], [('CERT1', 1)])
        self.assertEqual(evento[2].documentos, [])

    def test_conteudo_dos_documentos_e_ignorado(self):
        p = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO).processo
        for mov in p.movimentos:
            for doc in mov.documentos:
                self.assertFalse(hasattr(doc, 'conteudo'))
        self.assertNotIn('Q09OVEVVRE8', repr(p))

    def test_nome_do_documento_usa_rotulo_depois_descricao_depois_id(self):
        self.assertEqual(mni.Documento(id='9', rotulo='CERT1', descricao='CERTIDÃO').nome, 'CERT1')
        self.assertEqual(mni.Documento(id='9', descricao='CERTIDÃO').nome, 'CERTIDÃO')
        self.assertEqual(mni.Documento(id='9').nome, 'Documento 9')

    def test_resposta_grande_demais_repete_sem_documentos(self):
        respostas = [mni.RespostaGrandeDemais('grande'), RESPOSTA_PROCESSO]
        with mock.patch.object(mni.ClienteMNI, '_enviar', side_effect=respostas) as enviar:
            r = mni.consultar_processo(self.ambiente, '1', 'h', NUMERO)
        self.assertTrue(r.documentos_omitidos)
        self.assertIn(b'incluirDocumentos>true<', enviar.call_args_list[0].args[1])
        self.assertIn(b'incluirDocumentos>false<', enviar.call_args_list[1].args[1])
        self.assertEqual(r.processo.numero_formatado, NUMERO_FORMATADO)

    def test_formatacoes(self):
        self.assertEqual(mni.formatar_oab('TO0001234A'), 'OAB/TO 1234-A')
        self.assertEqual(mni.formatar_oab('invalido'), 'invalido')
        self.assertEqual(mni.formatar_documento('12345678909'), '123.456.789-09')
        self.assertEqual(mni.formatar_documento('RG 123'), 'RG 123')

    def test_movimentos_sem_data_nao_quebram_a_ordenacao(self):
        conteudo = RESPOSTA_PROCESSO.replace(b'dataHora="2025-06-01T09:00:00-03:00" ', b'')
        movs = mni.ClienteMNI300.interpretar_processo(conteudo).processo.movimentos
        self.assertIsNone(movs[-1].data_hora)

    def test_nao_encontrado(self):
        r = mni.ClienteMNI300.interpretar_processo(RESPOSTA_NAO_ENCONTRADO)
        self.assertFalse(r.sucesso)
        self.assertIsNone(r.processo)
        self.assertFalse(mni.falha_de_autenticacao(r))


class Base(TestCase):
    SENHA = 'S3nh@'

    def setUp(self):
        cache.clear()
        p = mock.patch.object(wsdl, '_baixar', return_value=WSDL)
        p.start()
        self.addCleanup(p.stop)
        self.user = get_user_model().objects.create_user('ana', email='ana@exemplo.com', password='x')
        self.client.force_login(self.user)
        self.eproc = SistemaJudicial.objects.get(nome='EPROC TJTO')
        self.amb1, self.amb2 = self.eproc.ambientes.order_by('ordem')

    def informar_senha(self):
        self.client.post(reverse('sistemas:informar_senha', args=[self.eproc.pk]), {'login': '123', 'senha': self.SENHA})

    def url_processo(self, amb, numero=NUMERO):
        return reverse('sistemas:processo', args=[amb.sistema_id, amb.pk, numero])

    def url_dados(self, amb, numero=NUMERO):
        return reverse('sistemas:processo_dados', args=[amb.sistema_id, amb.pk, numero])


class BuscaNaBarraTests(Base):
    def buscar(self, numero, **extra):
        return self.client.get(reverse('sistemas:buscar_processo'), {'numero': numero}, **extra)

    def test_campo_aparece_na_barra_do_topo(self):
        resp = self.client.get(reverse('inicio'))
        self.assertContains(resp, f'action="{reverse("sistemas:buscar_processo")}"')
        self.assertContains(resp, 'name="numero"')

    def test_usa_o_ambiente_em_uso(self):
        self.client.get(reverse('sistemas:servicos', args=[self.eproc.pk, self.amb2.pk]))
        self.assertRedirects(self.buscar(NUMERO_FORMATADO), self.url_processo(self.amb2), fetch_redirect_response=False)

    def test_sem_ambiente_em_uso_usa_o_padrao(self):
        definir_ambiente_padrao(self.user, self.amb1)
        self.assertRedirects(self.buscar(NUMERO), self.url_processo(self.amb1), fetch_redirect_response=False)

    def test_sem_ambiente_pede_para_escolher(self):
        resp = self.buscar(NUMERO, follow=True)
        self.assertRedirects(resp, reverse('sistemas:lista'))
        self.assertContains(resp, 'Escolha o sistema e o ambiente de trabalho')

    def test_numero_invalido_volta_com_mensagem(self):
        definir_ambiente_padrao(self.user, self.amb1)
        resp = self.buscar('0001234-15.2024.8.27.2729', HTTP_REFERER='http://testserver' + reverse('sistemas:lista'), follow=True)
        self.assertRedirects(resp, reverse('sistemas:lista'))
        self.assertContains(resp, 'dígito verificador não confere')
        self.assertContains(resp, 'value="0001234-15.2024.8.27.2729"')  # mantém o que foi digitado
        self.assertNotContains(self.client.get(reverse('inicio')), 'value="0001234-15')  # só uma vez

    def test_referer_externo_e_ignorado(self):
        resp = self.buscar('123', HTTP_REFERER='https://malicioso.com/')
        self.assertRedirects(resp, reverse('inicio'), fetch_redirect_response=False)

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.buscar(NUMERO).status_code, 302)


class PaginaProcessoTests(Base):
    def test_numero_invalido_na_url_da_404(self):
        self.assertEqual(self.client.get(self.url_processo(self.amb1, '00012341520248272729')).status_code, 404)
        self.assertEqual(self.client.get(self.url_dados(self.amb1, 'abc')).status_code, 404)

    def test_sem_senha_pede_senha(self):
        resp = self.client.get(self.url_processo(self.amb1))
        self.assertContains(resp, f'Processo {NUMERO_FORMATADO}')
        self.assertContains(resp, 'A consulta do processo começa assim que você informar a senha')
        self.assertNotContains(resp, 'hx-trigger="load"')
        self.assertContains(resp, f'value="{NUMERO_FORMATADO}"')  # número fica no campo da barra

    def test_com_senha_carrega_em_segundo_plano(self):
        self.informar_senha()
        resp = self.client.get(self.url_processo(self.amb1))
        self.assertContains(resp, f'hx-get="{self.url_dados(self.amb1)}"')
        self.assertEqual(self.client.session['ambiente_id'], self.amb1.pk)

    def test_dados_do_processo(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_PROCESSO)) as urlopen:
            resp = self.client.get(self.url_dados(self.amb1))
        corpo = urlopen.call_args_list[0].args[0].data.decode()  # 1ª chamada: consultarProcesso
        self.assertIn('requisicaoConsultarProcesso', corpo)
        self.assertIn(sha256(self.SENHA.encode('utf-8')).hexdigest(), corpo)
        for texto in (NUMERO_FORMATADO, '1ª Vara Cível de Palmas', 'RÉU', 'FULANO DE TAL',
                      '(123.456.789-09)', 'Pessoa Física', 'BELTRANA ADVOGADA', 'OAB/TO 1234',
                      'Terceiro', 'DELEGACIA DE POLÍCIA DE PALMAS', 'PROCURADOR DO ESTADO',
                      'Indenização por Dano Moral', 'R$ 15.000,50', '2024.0001234-01 DP Palmas', 'Idoso',
                      'Informações adicionais', 'Rito processual', 'Ordinário', 'Antecipação de tutela',
                      'Procedimento Comum Cível', 'title="Código 7"', 'MOVIMENTO', '15/03/2024 14:22:33',
                      'Responsabilidade Civil, DIREITO CIVIL', 'Cível',
                      'Distribuído por sorteio', 'INIC1', 'PROC1', 'ANEXO2', 'CERT1', 'Evento não gerou documento',
                      'Pesquisar nos eventos'):
            self.assertContains(resp, texto)
        self.assertContains(resp, 'Eventos <span class="font-normal text-subtle text-sm">(3)</span>', html=False)
        self.assertContains(resp, 'title="Abrir PETIÇÃO INICIAL · 15/03/2024 14:20 · 43 KB"')
        # representantes aparecem sem descrição de cargo/função
        for cargo in ('Advocacia pública', 'Advogado(a)', 'Representante:'):
            self.assertNotContains(resp, cargo)
        self.assertNotContains(resp, 'https://')

    def test_processo_nao_encontrado(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_NAO_ENCONTRADO)):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, 'Processo não encontrado')
        self.assertContains(resp, 'Confira o número')

    def test_senha_recusada_descarta_e_pede_de_novo(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_FALHA_AUTH)):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, 'Senha ou usuário Inválidos')
        self.assertContains(resp, 'senha recusada pelo tribunal')
        with mock.patch('urllib.request.urlopen') as urlopen:
            self.client.get(self.url_dados(self.amb1))
        urlopen.assert_not_called()

    def test_erro_de_conexao(self):
        self.informar_senha()
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('x')):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, 'Não foi possível conectar ao tribunal')

    def test_avisos_tem_link_para_o_processo(self):
        from .test_avisos import RESPOSTA_OK

        self.informar_senha()
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_OK)):
            resp = self.client.get(reverse('sistemas:avisos', args=[self.eproc.pk, self.amb1.pk]))
        self.assertContains(resp, f'href="{self.url_processo(self.amb1)}"')


RESPOSTA_COMPETENCIAS = (DADOS / 'competencias_resposta.xml').read_bytes()


class CompetenciasTests(Base):
    def test_interpreta_tabela(self):
        tabela = mni.ClienteMNI300.interpretar_competencias(RESPOSTA_COMPETENCIAS)
        self.assertEqual(len(tabela), 16)
        self.assertEqual(tabela['5'], 'CRIMINAL')
        self.assertEqual(tabela['14'], 'JUSTIÇA MILITAR / CONSELHO PERMANENTE')

    def test_processo_traz_codigo_da_localidade(self):
        self.assertEqual(mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO).processo.codigo_localidade, '2729')

    def test_envelope_sem_autenticacao(self):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_COMPETENCIAS)) as urlopen:
            mni.cliente(self.amb1).consultar_competencias('2729')
        req = urlopen.call_args.args[0]
        corpo = req.data.decode()
        self.assertEqual(req.get_header('Soapaction'), '"http://www.cnj.jus.br/mni/v300/consultarCompetencias"')
        self.assertIn('requisicaoConsultarCompetencias', corpo)
        self.assertIn('codigoLocalidade>2729<', corpo)
        self.assertNotIn('consultante', corpo)

    def test_tabela_fica_em_cache_por_ambiente_e_localidade(self):
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(RESPOSTA_COMPETENCIAS)) as urlopen:
            mni.cliente(self.amb1).consultar_competencias('2729')
            mni.cliente(self.amb1).consultar_competencias('2729')
            self.assertEqual(urlopen.call_count, 1)
            mni.cliente(self.amb1).consultar_competencias('9999')
            mni.cliente(self.amb2).consultar_competencias('2729')
            self.assertEqual(urlopen.call_count, 3)

    def test_tabela_vazia_nao_fica_em_cache(self):
        vazia = RESPOSTA_COMPETENCIAS.split(b'<ns2:competencias>')[0] + b'</ns3:respostaConsultarCompetencias></SOAP-ENV:Body></SOAP-ENV:Envelope>'
        with mock.patch('urllib.request.urlopen', return_value=_Resposta(vazia)) as urlopen:
            self.assertEqual(mni.cliente(self.amb1).consultar_competencias('1'), {})
            mni.cliente(self.amb1).consultar_competencias('1')
        self.assertEqual(urlopen.call_count, 2)

    def test_pagina_mostra_a_descricao_da_competencia(self):
        self.informar_senha()
        respostas = [_Resposta(RESPOSTA_PROCESSO), _Resposta(RESPOSTA_COMPETENCIAS)]
        with mock.patch('urllib.request.urlopen', side_effect=respostas) as urlopen:
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertEqual(urlopen.call_count, 2)
        self.assertContains(resp, 'JUSTIÇA MILITAR / CONSELHO PERMANENTE')
        self.assertContains(resp, 'title="Código 14"')

    def test_aviso_quando_documentos_foram_omitidos(self):
        self.informar_senha()
        with mock.patch.object(mni.ClienteMNI, '_enviar', side_effect=[mni.RespostaGrandeDemais('x'), RESPOSTA_PROCESSO, RESPOSTA_COMPETENCIAS]):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, 'muito grande para trazer a lista de documentos')
        self.assertContains(resp, NUMERO_FORMATADO)

    def test_falha_nas_competencias_nao_impede_o_processo(self):
        self.informar_senha()
        respostas = [_Resposta(RESPOSTA_PROCESSO), urllib.error.URLError('fora do ar')]
        with mock.patch('urllib.request.urlopen', side_effect=respostas):
            resp = self.client.get(self.url_dados(self.amb1))
        self.assertContains(resp, NUMERO_FORMATADO)
        self.assertContains(resp, 'title="Código 14">14<', html=False)
        self.assertNotContains(resp, 'Não foi possível conectar')


class PaginacaoEventosTests(Base):
    """A tabela de eventos é paginada no navegador (judicial/eventos.js): padrão 35 por página."""

    def dados_com_eventos(self, quantidade):
        resultado = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO)
        base = resultado.processo.movimentos[0]
        resultado.processo.movimentos = [
            mni.Movimento(data_hora=base.data_hora, descricao=f'Evento de teste {n}', numero=n)
            for n in range(quantidade, 0, -1)
        ]
        self.informar_senha()
        with mock.patch.object(mni, 'consultar_processo', return_value=resultado), \
                mock.patch.object(mni, 'descrever_competencia'):
            return self.client.get(self.url_dados(self.amb1))

    def test_seletor_com_as_opcoes_e_35_por_padrao(self):
        resp = self.dados_com_eventos(120)
        self.assertContains(resp, 'x-data="eventosPaginados()"')
        self.assertContains(resp, 'x-model.number="porPagina"')
        for valor, rotulo in ((10, '10'), (25, '25'), (50, '50'), (100, '100'), (0, 'Tudo')):
            self.assertContains(resp, f'<option value="{valor}">{rotulo}</option>', html=False)
        self.assertContains(resp, '<option value="35" selected>35</option>')

    def test_todos_os_eventos_vem_numa_unica_consulta(self):
        # A paginação só esconde linhas: trocar de página nunca consulta o tribunal de novo.
        resp = self.dados_com_eventos(120)
        self.assertContains(resp, 'Evento de teste ', count=120)
        self.assertContains(resp, 'Primeira página')
        self.assertContains(resp, 'Última página')

    def test_poucos_eventos_nao_mostram_o_seletor(self):
        resp = self.dados_com_eventos(10)
        self.assertNotContains(resp, 'x-model.number="porPagina"')

    def test_pagina_do_processo_carrega_o_script(self):
        self.informar_senha()
        self.assertContains(self.client.get(self.url_processo(self.amb1)), 'judicial/eventos.js')


class CapaDoProcessoTests(Base):
    def dados(self, processo_sem_prioridade=False):
        resultado = mni.ClienteMNI300.interpretar_processo(RESPOSTA_PROCESSO)
        if processo_sem_prioridade:
            resultado.processo.prioridades_tribunal = []
            resultado.processo.prioridades = []
        self.informar_senha()
        with mock.patch.object(mni, 'consultar_processo', return_value=resultado), \
                mock.patch.object(mni, 'descrever_competencia'):
            return self.client.get(self.url_dados(self.amb1))

    def cabecalho(self, resp):
        html = resp.content.decode()
        inicio = html.index('<section')  # 1º card: capa do processo
        return html[inicio:html.index('<dl', inicio)]

    def test_prioridades_no_cabecalho_sem_o_rotulo(self):
        resp = self.dados()
        cabecalho = self.cabecalho(resp)
        self.assertIn('Idoso', cabecalho)
        self.assertIn('Cível', cabecalho)
        self.assertNotContains(resp, '>Prioridade<')

    def test_cabecalho_sem_horario_da_consulta(self):
        resp = self.dados()
        self.assertNotContains(resp, 'Consultado às')
        self.assertIn('Atualizar', self.cabecalho(resp))

    def test_sem_prioridade_o_cabecalho_fica_so_com_o_botao(self):
        cabecalho = self.cabecalho(self.dados(processo_sem_prioridade=True))
        self.assertNotIn('Idoso', cabecalho)
        self.assertIn('Atualizar', cabecalho)
