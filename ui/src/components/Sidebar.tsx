import type { ReactNode } from 'react';
import type { Route } from '../app/router';
import { DEFAULT_EXECUTION_ID } from '../app/router';
import type { ShellVM } from '../contracts/viewmodels';
import { BrandMark, IconArrow, IconCheck, IconCog, IconHome, IconSearch, IconShield } from './Icons';
import { StateGlyph } from './StatusParts';

function NavLink({ href, on, children }: { href: string; on: boolean; children: ReactNode }) {
  return (
    <a href={href} className={on ? 'on' : undefined} aria-current={on ? 'page' : undefined}>
      {children}
    </a>
  );
}

export function Sidebar({ shell, route }: { shell: ShellVM | undefined; route: Route }) {
  return (
    <aside>
      <div className="brand">
        <BrandMark />
        <div>
          <b>IAOS</b>
          <span>Sistema pessoal de agentes</span>
        </div>
      </div>
      <div className="search">
        <IconSearch />
        Buscar
      </div>
      <div className="eyebrow">Execuções recentes</div>
      <div className="recent">
        {shell?.recents.map((r) => {
          const on = route.name === 'execution' && route.id === r.id;
          return (
            <a key={r.id} className={`rec${on ? ' on' : ''}`} href={`#/execution/${r.id}`}>
              <StateGlyph state={r.state} />
              <div>
                <b>{r.title}</b>
                <small>
                  {r.stateLabel} · {r.duration}
                </small>
              </div>
            </a>
          );
        })}
      </div>
      <nav className="nav">
        <NavLink href="#/" on={route.name === 'home'}>
          <IconHome />
          Início
        </NavLink>
        <NavLink href="#/daily-brief" on={route.name === 'daily-brief'}>
          <IconCheck />
          Daily Brief
        </NavLink>
        <NavLink href="#/scouts" on={route.name === 'scouts'}>
          <IconSearch />
          Scouts
        </NavLink>
        <NavLink href="#/skills" on={route.name === 'skills'}>
          <IconArrow />
          Skills
        </NavLink>
        <NavLink href="#/authority" on={route.name === 'authority'}>
          <IconShield />
          Autoridade &amp; Capacidades
        </NavLink>
        <NavLink href={`#/execution/${DEFAULT_EXECUTION_ID}`} on={route.name === 'execution'}>
          <IconCheck />
          Auditoria
        </NavLink>
        <NavLink href="#/settings/models" on={route.name === 'settings-models' || route.name === 'settings-google'}>
          <IconCog />
          Configurações
        </NavLink>
      </nav>
    </aside>
  );
}
