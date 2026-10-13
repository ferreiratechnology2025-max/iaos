import type { ModelLocation } from '../contracts/viewmodels';
import { LOCATION_DISCLOSURE } from './modelCopy';

type Variant = 'disc' | 'note';

/** Privacy disclosure that differs for Local, LAN and Cloud. */
export function Disclosure({ location, variant }: { location: ModelLocation; variant: Variant }) {
  const [title, text] = LOCATION_DISCLOSURE[location];
  const local = location === 'LOCAL';
  const cls = variant === 'disc' ? `disc${local ? ' ok' : ''}` : `note${local ? '' : ' warnnote'}`;
  return (
    <div className={cls}>
      <b>{title}.</b> {text}
    </div>
  );
}
