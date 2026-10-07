import { createContext, useContext } from 'react'
import type { Generation } from '@/lib/types'

/**
 * What every part of the Workspace screen needs to know about the open draft.
 * Provided once by <Workspace>, so claims nested deep inside the documents do
 * not need these passed down through every section and entry.
 */
export interface WorkspaceContextValue {
  generation: Generation
  /** The number shown on each evidence badge, by evidence ID. */
  evidenceNumbers: ReadonlyMap<string, number>
  /** The evidence record currently shown in the evidence panel, if any. */
  selectedEvidenceId: string | null
  selectEvidence: (evidenceId: string) => void
  /** Switch to the document that contains this claim, scroll to it and focus it. */
  revealClaim: (itemId: string) => void
  /** A change to this draft is being saved. Other changes wait, so revisions never collide. */
  isWriting: boolean
}

export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace must be used inside <Workspace>')
  return value
}
