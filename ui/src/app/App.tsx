import { dataSource } from '../data';
import { Sidebar } from '../components/Sidebar';
import { AuthorityScreen } from '../screens/authority/AuthorityScreen';
import { ExecutionScreen } from '../screens/execution/ExecutionScreen';
import { HomeScreen } from '../screens/home/HomeScreen';
import { SettingsModelsScreen } from '../screens/settings-models/SettingsModelsScreen';
import { SettingsGoogleScreen } from '../screens/settings-google/SettingsGoogleScreen';
import { DailyBriefScreen } from '../screens/daily-brief/DailyBriefScreen';
import { DailyBriefHistoryScreen } from '../screens/daily-brief/DailyBriefHistoryScreen';
import { SkillsScreen } from '../screens/skills/SkillsScreen';
import { ModelProvider } from './ModelContext';
import { useRoute } from './router';
import { useData } from './useData';
import { ScoutsScreen } from '../screens/scouts/ScoutsScreen';

export function App() {
  const route = useRoute();
  const shell = useData(() => dataSource.getShell(), []);
  return (
    <ModelProvider>
      <div className="app">
        <Sidebar shell={shell} route={route} />
        <main>
          {route.name === 'home' && <HomeScreen workspacePath={shell?.workspacePath ?? ''} />}
          {route.name === 'authority' && <AuthorityScreen />}
          {route.name === 'skills' && <SkillsScreen />}
          {route.name === 'execution' && <ExecutionScreen id={route.id} />}
          {route.name === 'settings-models' && <SettingsModelsScreen />}
          {route.name === 'settings-google' && <SettingsGoogleScreen />}
          {route.name === 'daily-brief' && <DailyBriefScreen />}
          {route.name === 'daily-brief-history' && <DailyBriefHistoryScreen runId={route.runId} />}
          {route.name === 'scouts' && <ScoutsScreen />}
        </main>
      </div>
    </ModelProvider>
  );
}
