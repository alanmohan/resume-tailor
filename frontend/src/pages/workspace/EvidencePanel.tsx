import { useQuery } from '@tanstack/react-query'
import { Quote, SearchX } from 'lucide-react'
import { EmptyState, ErrorAlert, LoadingBlock, SourceExcerpt } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { getEvidence } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { queryKeys } from '@/lib/queryKeys'
import type { Evidence, Provenance } from '@/lib/types'
import { useWorkspace } from './workspaceContext'
import { DOCUMENT_LABEL, evidenceTitle, listClaims } from './workspaceModel'

const SOURCE_TYPE_LABEL: Record<string, string> = {
  resume: 'Resume',
  linkedin: 'LinkedIn profile',
  notes: 'Notes',
}

const PROVENANCE_LABEL: Record<Provenance, string> = {
  extracted: 'Extracted from your source',
  user_edited: 'Edited by you',
  user_added: 'Added by you',
}

const PARENT_CATEGORY_LABEL: Record<string, string> = {
  employment: 'role',
  project: 'project',
  publication: 'publication',
  achievement: 'achievement',
  education: 'education',
  certification: 'certification',
  skill: 'skill group',
}

/** "Software Engineer at Northwind Robotics (role)" */
function parentLabel(parent: NonNullable<Evidence['parent']>): string {
  const name = parent.organization ? `${parent.title} at ${parent.organization}` : parent.title
  return `${name} (${PARENT_CATEGORY_LABEL[parent.category] ?? parent.category})`
}

function EvidenceRecord({ evidence }: { evidence: Evidence }) {
  const { generation, revealClaim } = useWorkspace()
  const citedBy = listClaims(generation).filter(({ claim }) =>
    claim.evidence_ids.includes(evidence.evidence_id),
  )
  const supports = generation.coverage.filter((item) =>
    item.evidence_ids.includes(evidence.evidence_id),
  )
  const { source, parent, tags } = evidence
  const sourceType = SOURCE_TYPE_LABEL[source.source_type] ?? source.source_type

  return (
    <div className="space-y-5">
      {/* The excerpt is untrusted text; SourceExcerpt renders it as a plain text node. */}
      <SourceExcerpt
        excerpt={evidence.excerpt || evidence.text}
        label={source.label}
        detail={`Source type: ${sourceType}`}
      />

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">Belongs to</dt>
        <dd className="wrap-anywhere">
          {parent ? parentLabel(parent) : 'Not linked to a role or project'}
        </dd>
        <dt className="text-muted-foreground">Provenance</dt>
        <dd>{PROVENANCE_LABEL[evidence.provenance]}</dd>
        {tags.length > 0 ? (
          <>
            <dt className="text-muted-foreground">Tags</dt>
            <dd className="flex flex-wrap gap-1">
              {tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="h-auto max-w-full whitespace-normal">
                  {tag}
                </Badge>
              ))}
            </dd>
          </>
        ) : null}
      </dl>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Statements that cite this</h3>
        {citedBy.length === 0 ? (
          <p className="text-sm text-muted-foreground">No statement in this draft cites this record.</p>
        ) : (
          <ul className="space-y-1.5">
            {citedBy.map(({ claim, documentKind, place }) => (
              <li key={claim.item_id}>
                <Button
                  type="button"
                  variant="outline"
                  className="h-auto w-full flex-col items-start gap-0.5 px-2.5 py-2 text-left font-normal whitespace-normal"
                  onClick={() => revealClaim(claim.item_id)}
                >
                  <span className="sr-only">Go to statement: </span>
                  <span className="text-xs text-muted-foreground">
                    {DOCUMENT_LABEL[documentKind]} - {place}
                  </span>
                  <span className="line-clamp-2 font-serif wrap-anywhere">{claim.text}</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {supports.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Requirements it is cited for</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {supports.map((item) => (
              <li key={item.requirement_id} className="wrap-anywhere">
                {item.requirement_text}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

/**
 * One evidence record, loaded on demand: the exact supporting excerpt, its
 * source, the role or project it belongs to, its provenance and where the
 * draft cites it. Only what the API returns is shown; embedding vectors and
 * retrieval scores never reach the browser.
 */
export function EvidenceDetail({ evidenceId }: { evidenceId: string }) {
  const query = useQuery({
    queryKey: queryKeys.evidence(evidenceId),
    queryFn: () => getEvidence(evidenceId),
    // A stored evidence record never changes, so it is fetched once per page visit.
    staleTime: Infinity,
  })

  if (query.data) return <EvidenceRecord evidence={query.data} />
  if (isApiError(query.error, 'not_found')) {
    return (
      <EmptyState
        icon={SearchX}
        title="This evidence is no longer available"
        description="The record was not found. It may have been removed when your data was cleared."
      />
    )
  }
  if (query.isError) {
    return (
      <ErrorAlert
        error={query.error}
        title="The evidence could not be loaded"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    )
  }
  return <LoadingBlock label="Loading evidence..." />
}

/** The evidence tab of the side panel: the selected record, or a hint when nothing is selected. */
export function EvidencePanel() {
  const { selectedEvidenceId, evidenceNumbers } = useWorkspace()

  if (!selectedEvidenceId) {
    return (
      <EmptyState
        icon={Quote}
        title="No evidence selected"
        description="Select a numbered badge next to a statement or requirement to read the exact text that supports it."
      />
    )
  }
  return (
    <div className="space-y-4">
      <h2 className="text-base font-medium">{evidenceTitle(evidenceNumbers, selectedEvidenceId)}</h2>
      <EvidenceDetail evidenceId={selectedEvidenceId} />
    </div>
  )
}
