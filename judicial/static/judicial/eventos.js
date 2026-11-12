// Busca e paginação da tabela de eventos do processo.
// Tudo acontece no navegador: o processo já foi consultado (uma única vez) no tribunal,
// então trocar de página nunca gera nova consulta.

const EVENTOS_OPCOES = [10, 25, 35, 50, 100, 0]; // 0 = Tudo
const EVENTOS_PADRAO = 35;
const EVENTOS_CHAVE = 'elojudicial:eventosPorPagina';

function eventosPorPaginaSalvo() {
    try {
        const valor = parseInt(localStorage.getItem(EVENTOS_CHAVE), 10);
        if (EVENTOS_OPCOES.includes(valor)) return valor;
    } catch (e) { /* navegador sem armazenamento local: usa o padrão */ }
    return EVENTOS_PADRAO;
}

function guardarEventosPorPagina(valor) {
    try { localStorage.setItem(EVENTOS_CHAVE, String(valor)); } catch (e) { /* ignora */ }
}

/**
 * Decide quais linhas aparecem. Função pura (testável sem navegador).
 * textos: texto de cada linha, em minúsculas; porPagina: 0 = todas.
 */
function paginarEventos(textos, busca, porPagina, pagina) {
    const termo = (busca || '').trim().toLowerCase();
    const encontrados = [];
    textos.forEach((texto, i) => { if (!termo || texto.includes(termo)) encontrados.push(i); });

    const total = encontrados.length;
    const totalPaginas = porPagina > 0 ? Math.max(1, Math.ceil(total / porPagina)) : 1;
    const atual = Math.min(Math.max(1, pagina || 1), totalPaginas);
    const inicio = porPagina > 0 ? (atual - 1) * porPagina : 0;
    const fim = porPagina > 0 ? Math.min(inicio + porPagina, total) : total;

    const visiveis = new Array(textos.length).fill(false);
    encontrados.slice(inicio, fim).forEach((i) => { visiveis[i] = true; });
    return { visiveis, total, totalPaginas, pagina: atual, inicio: total ? inicio + 1 : 0, fim };
}

// Componente Alpine: x-data="eventosPaginados()" na seção, x-ref="corpo" no <tbody>.
function eventosPaginados() {
    return {
        busca: '',
        porPagina: eventosPorPaginaSalvo(),
        pagina: 1,
        total: 0,
        totalPaginas: 1,
        inicio: 0,
        fim: 0,
        linhas: [],

        init() {
            this.linhas = Array.from(this.$refs.corpo.rows, (tr) => ({ tr, texto: tr.textContent.toLowerCase() }));
            this.$watch('busca', () => { this.pagina = 1; this.aplicar(); });
            this.$watch('porPagina', (valor) => {
                guardarEventosPorPagina(valor);
                this.pagina = 1;
                this.aplicar();
            });
            this.aplicar();
        },

        aplicar() {
            const r = paginarEventos(this.linhas.map((l) => l.texto), this.busca, this.porPagina, this.pagina);
            this.linhas.forEach((l, i) => { l.tr.hidden = !r.visiveis[i]; });
            this.pagina = r.pagina;
            this.total = r.total;
            this.totalPaginas = r.totalPaginas;
            this.inicio = r.inicio;
            this.fim = r.fim;
        },

        irPara(pagina) {
            this.pagina = pagina;
            this.aplicar();
            this.$root.scrollIntoView({ block: 'start', behavior: 'smooth' });
        },

        get resumo() {
            const nome = this.busca.trim() ? 'encontrado' : 'evento';
            const plural = this.total === 1 ? nome : nome + 's';
            if (!this.total) return '';
            if (this.porPagina === 0 || this.totalPaginas === 1) return `${this.total} ${plural}`;
            return `Mostrando ${this.inicio}–${this.fim} de ${this.total} ${plural}`;
        },
    };
}

if (typeof module !== 'undefined') {
    module.exports = { paginarEventos, eventosPorPaginaSalvo, EVENTOS_OPCOES, EVENTOS_PADRAO };
}
