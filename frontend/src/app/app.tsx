import { ArrowDown, Layers2, Moon, ShieldCheck, Sparkle, Sun } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ApiStatus } from '@/features/system/api-status';
import { ProjectsWorkspace } from '@/features/projects/projects-workspace';
import { useTheme } from './use-theme';

function EdivoMark() {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" className="edivo-mark">
      <path d="M8 8h18l-4 5H8V8Zm0 8h14l-4 5H8v-5Zm0 8h10l-4 5H8v-5Z" fill="currentColor" />
      <path d="M3 3h5v26H3V3Z" fill="currentColor" />
    </svg>
  );
}

export function App() {
  const { theme, toggleTheme } = useTheme();
  const [editorMode, setEditorMode] = useState(false);

  return (
    <div className={`app-shell${editorMode ? ' editor-shell' : ''}`}>
      <a className="skip-link" href="#main-content">
        Pular para o conteúdo
      </a>
      <header className="site-header">
        <a className="brand" href="#main-content" aria-label="Edivo — início">
          <EdivoMark />
          <span>
            edivo<span className="brand-dot">.</span>
          </span>
        </a>
        <div className="header-tools">
          <span className="workspace-label">
            <span aria-hidden="true" />
            Ambiente local
          </span>
          <span className="header-divider" aria-hidden="true" />
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro'}
            title={theme === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro'}
          >
            {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </Button>
        </div>
      </header>

      <main id="main-content" tabIndex={-1}>
        {!editorMode && (
          <>
            <div className="page-eyebrow">
              <span className="eyebrow-line" />
              SEU ESPAÇO DE CRIAÇÃO
            </div>
            <div className="hero-grid">
              <section className="hero-copy" aria-labelledby="welcome-title">
                <p className="release-badge">
                  <span>R04</span> Textos, áudio e prévias locais
                </p>
                <h1 id="welcome-title">
                  Boas histórias.
                  <br />
                  Um novo<span className="accent-word"> começo.</span>
                </h1>
                <p className="hero-description">
                  Este é o Edivo. Um editor de vídeo web pensado para transformar suas ideias em
                  conteúdo, no seu próprio ambiente.
                </p>
                <div className="stage-note">
                  <div className="stage-icon">
                    <Layers2 size={19} aria-hidden="true" />
                  </div>
                  <div>
                    <h2>Comece pelos seus originais.</h2>
                    <p>
                      Organize projetos, importe seus vídeos e abra o editor para conhecer seus
                      originais.
                    </p>
                  </div>
                </div>
                <a className="text-link" href="#/projects">
                  Abrir meus projetos <ArrowDown size={15} aria-hidden="true" />
                </a>
              </section>
              <ApiStatus />
            </div>
          </>
        )}

        <ProjectsWorkspace onEditorModeChange={setEditorMode} />

        {!editorMode && (
          <section
            className="principles-section"
            id="about-edivo"
            aria-labelledby="principles-title"
          >
            <div className="principles-heading">
              <p className="section-kicker">MENOS DISTÂNCIA ENTRE IDEIA E VÍDEO</p>
              <h2 id="principles-title">Um espaço para o que vem a seguir.</h2>
            </div>
            <div className="principles-grid">
              <article className="principle">
                <span className="principle-number">01 /</span>
                <div className="principle-title">
                  <ShieldCheck size={20} aria-hidden="true" />
                  <h3>Local por princípio.</h3>
                </div>
                <p>
                  A proposta é simples: criar no seu ambiente, com seus arquivos sob seu controle.
                </p>
              </article>
              <article className="principle">
                <span className="principle-number">02 /</span>
                <div className="principle-title">
                  <Layers2 size={20} aria-hidden="true" />
                  <h3>Uma base consistente.</h3>
                </div>
                <p>
                  Primeiro, uma fundação confiável. Depois, os recursos para dar forma às suas
                  ideias.
                </p>
              </article>
              <article className="principle">
                <span className="principle-number">03 /</span>
                <div className="principle-title">
                  <Sparkle size={20} aria-hidden="true" />
                  <h3>Espaço para evoluir.</h3>
                </div>
                <p>
                  Projetos organizados, reprodução privada e originais preservados em cada escolha.
                </p>
              </article>
            </div>
          </section>
        )}
      </main>

      <footer className="site-footer">
        <p>
          Edivo<span aria-hidden="true"> / </span>Feito para suas próximas histórias.
        </p>
        <span>R04 · Desenvolvimento local</span>
      </footer>
    </div>
  );
}
