import {createContext, useContext} from 'react';

export const ToolNamesContext = createContext<Record<string, string>>({});
export const useToolNames = () => useContext(ToolNamesContext);

export const WorkspaceContext = createContext('default');
export const useWorkspaceId = () => useContext(WorkspaceContext);
