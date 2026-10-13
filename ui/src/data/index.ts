import type { IaosDataPort } from './ports/ports';
import { FixtureDataSource } from './fixtures/FixtureDataSource';
import type { SkillRuntimePort } from './ports/skills';
import { HttpSkillRuntime } from './skills/HttpSkillRuntime';
import { RealIaosDataSource } from './real/RealIaosDataSource';

/**
 * Production is real-only. Fixtures are opt-in for screenshots/tests.
 */
const demoMode = import.meta.env.VITE_IAOS_DEMO_MODE === 'true';
export const dataSource: IaosDataPort = demoMode ? new FixtureDataSource() : new RealIaosDataSource();

/** Skill runtime: real Skills run through the loopback bridge (see HttpSkillRuntime). */
export const skillRuntime: SkillRuntimePort = new HttpSkillRuntime();
