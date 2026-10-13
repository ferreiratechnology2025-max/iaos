import type {
  AuthorityVM,
  ExecutionViewModel,
  HomeVM,
  ModelChecks,
  ModelProfileVM,
  ModelSettingsVM,
  ShellVM,
} from '../../contracts/viewmodels';

/**
 * Data port: the single boundary between the UI and whatever feeds it.
 * Today: FixtureDataSource. Later: an IAOS adapter, without touching screens.
 */
export interface IaosDataPort {
  getShell(): Promise<ShellVM>;
  getHome(): Promise<HomeVM>;
  getAuthority(capabilityId?: string): Promise<AuthorityVM>;
  getExecution(id: string): Promise<ExecutionViewModel | null>;
  getModelSettings(): Promise<ModelSettingsVM>;
  /**
   * Connection test. SIMULATED in fixtures. Whether a real test is an
   * administrative effect or a governed one is a pending decision
   * (TEST_CONNECTION_AUTHORITY_DECISION=PENDING).
   */
  testModelProfile(profile: ModelProfileVM): Promise<ModelChecks>;
}
