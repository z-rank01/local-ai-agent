import {createContext} from 'react';

export const BriefReaderContext = createContext<((path: string) => void) | null>(null);
