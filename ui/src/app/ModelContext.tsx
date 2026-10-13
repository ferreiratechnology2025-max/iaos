import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { ModelLocation, ModelProfileVM, ModelSettingsVM } from '../contracts/viewmodels';
import { dataSource } from '../data';

/**
 * Local UI state for model profiles (selection, drafts, simulated tests).
 * Provider = Cognition only. Nothing here grants execution authority.
 */
interface ModelState {
  ready: boolean;
  profiles: ModelProfileVM[];
  activeId: string;
  selectedId: string;
  dirtyIds: ReadonlySet<string>;
  testingId: string | null;
  popoverOpen: boolean;
  setActive(id: string): void;
  select(id: string): void;
  setPopoverOpen(open: boolean): void;
  edit(id: string, patch: Partial<ModelProfileVM>): void;
  setLocation(id: string, location: ModelLocation): void;
  save(id: string): void;
  test(id: string): void;
}

const Ctx = createContext<ModelState | null>(null);

export function ModelProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ModelSettingsVM | null>(null);
  const [selectedId, setSelectedId] = useState('atomic');
  const [dirty, setDirty] = useState<ReadonlySet<string>>(new Set());
  const [testingId, setTestingId] = useState<string | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);

  useEffect(() => {
    dataSource.getModelSettings().then((s) => {
      setSettings(s);
      setSelectedId(s.activeId);
    });
  }, []);

  const update = useCallback((id: string, fn: (p: ModelProfileVM) => ModelProfileVM) => {
    setSettings((s) => (s ? { ...s, profiles: s.profiles.map((p) => (p.id === id ? fn(p) : p)) } : s));
  }, []);

  const value = useMemo<ModelState>(
    () => ({
      ready: settings !== null,
      profiles: settings?.profiles ?? [],
      activeId: settings?.activeId ?? '',
      selectedId,
      dirtyIds: dirty,
      testingId,
      popoverOpen,
      setActive: (id) => setSettings((s) => (s ? { ...s, activeId: id } : s)),
      select: setSelectedId,
      setPopoverOpen,
      edit: (id, patch) => {
        update(id, (p) => ({ ...p, ...patch }));
        setDirty((d) => new Set(d).add(id));
      },
      setLocation: (id, location) => {
        update(id, (p) => ({ ...p, location }));
        setDirty((d) => new Set(d).add(id));
      },
      save: (id) => {
        update(id, (p) => ({ ...p, checks: null }));
        setDirty((d) => {
          const n = new Set(d);
          n.delete(id);
          return n;
        });
      },
      test: (id) => {
        const profile = settings?.profiles.find((p) => p.id === id);
        if (!profile) return;
        setTestingId(id);
        dataSource.testModelProfile(profile).then((checks) => {
          update(id, (p) => ({ ...p, checks }));
          setTestingId(null);
        });
      },
    }),
    [settings, selectedId, dirty, testingId, popoverOpen, update],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useModels(): ModelState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useModels outside ModelProvider');
  return v;
}
