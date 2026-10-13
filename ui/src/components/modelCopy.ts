import type { ModelLocation } from '../contracts/viewmodels';

/** Presentation copy for model locality. Not audit state. */
export const LOCATION_LABEL: Record<ModelLocation, string> = {
  LOCAL: 'Local',
  LAN: 'LAN',
  CLOUD: 'Cloud',
};

export const LOCATION_DISCLOSURE: Record<ModelLocation, [string, string]> = {
  LOCAL: ['Modelo local', 'Este modelo roda neste computador. As mensagens permanecem aqui.'],
  LAN: [
    'Modelo na rede local',
    'Este modelo roda em outro dispositivo da sua rede local. As mensagens sairão deste computador.',
  ],
  CLOUD: [
    'Modelo em nuvem',
    'Este modelo roda fora da sua rede local. As mensagens poderão ser enviadas a um provedor externo.',
  ],
};
