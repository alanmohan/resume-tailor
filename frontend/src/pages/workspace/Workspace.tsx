import { useRef, useState, type CSSProperties } from 'react'
import { flushSync } from 'react-dom'
import { Copy, Printer } from 'lucide-react'
import { cn } from 'cn'
import { toast } from 'sonner'
import { PageHeader } from '@/components/app'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { Generation } from '@/lib/types'
import { CoveragePanel } from './CoveragePanel'
import { CoverLetterDocument, ResumeDocument } from './Documents'
import { EvidenceDetail, EvidencePanel } from './EvidencePanel'
import { useIsWriting } from './generationData'
import { PrintGateDialog } from './PrintGateDialog'
import { StaleBanner } from './StaleBanner'
import { ValidationBar } from './ValidationBar'
import { WorkspaceContext } from './workspaceContext'
import { useMediaQuery } from './workspaceHooks'
import {
  DOCUMENT_KINDS,
  DOCUMENT_LABEL,
  claimElementId,
  documentPlainText,
  evidenceTitle,
  formatDateTime,
  isFlagged,
  jobLabel,
  listClaims,
  numberEvidence,
  type DocumentKind,
} from './workspaceModel'
import './workspace.css'

/**
 * Two panes from Tailwind's `lg` width up. "print" keeps that layout while
 * the browser lays the page out for paper, whose width would otherwise count
 * as a narrow screen and swap the layout in the middle of printing.
 */
const TWO_PANE_QUERY = 'print, (min-width: 64rem)'

const COVERAGE_TAB = 'coverage'
const SIDE_TABS = ['evidence', 'coverage'] as const
type SideTab = (typeof SIDE_TABS)[number]

/**
 * Tab panels stay mounted while another tab is shown, so an edit in progress
 * survives switching tabs and the print stylesheet can always reach the
 * active document. An inactive panel is taken out of the layout with an
 * inline style, which also keeps it out of the accessibility tree.
 */
function hiddenUnless(visible: boolean): CSSProperties | undefined {
  return visible ? undefined : { display: 'none' }
}

/**
 * The Workspace screen for a completed draft: the resume and cover letter on
 * the left, evidence and coverage on the right. On narrow screens coverage
 * becomes a third tab and evidence opens in a bottom sheet.
 */
export function Workspace({ generation }: { generation: Generation }) {
  const twoPane = useMediaQuery(TWO_PANE_QUERY)
  /** The document that Copy and Print act on: the one shown, or shown last. */
  const [activeDocument, setActiveDocument] = useState<DocumentKind>('resume')
  const [coverageTabOpen, setCoverageTabOpen] = useState(false)
  const [sideTab, setSideTab] = useState<SideTab>('coverage')
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null)
  const [evidenceSheetOpen, setEvidenceSheetOpen] = useState(false)
  const [printGateOpen, setPrintGateOpen] = useState(false)
  /** The control that opened the dialog or sheet, so focus can return to it. */
  const overlayOpener = useRef<HTMLElement | null>(null)
  /** A statement to go to once the open dialog or sheet has closed. */
  const claimToReveal = useRef<string | null>(null)

  const isWriting = useIsWriting(generation.generation_id)
  const claims = listClaims(generation)
  const flagged = claims.filter(({ claim }) => isFlagged(claim))
  const evidenceNumbers = numberEvidence(generation)
  const needsRevalidation = generation.validation.state === 'needs_revalidation'

  const showingCoverageTab = !twoPane && coverageTabOpen
  const mainTab = showingCoverageTab ? COVERAGE_TAB : activeDocument

  function handleMainTabChange(value: string) {
    const kind = DOCUMENT_KINDS.find((candidate) => candidate === value)
    if (kind) setActiveDocument(kind)
    setCoverageTabOpen(value === COVERAGE_TAB)
  }

  function handleSideTabChange(value: string) {
    const tab = SIDE_TABS.find((candidate) => candidate === value)
    if (tab) setSideTab(tab)
  }

  function rememberOverlayOpener() {
    const focused = document.activeElement
    overlayOpener.current = focused instanceof HTMLElement ? focused : null
  }

  function selectEvidence(evidenceId: string) {
    setSelectedEvidenceId(evidenceId)
    if (twoPane) {
      setSideTab('evidence')
    } else {
      rememberOverlayOpener()
      setEvidenceSheetOpen(true)
    }
  }

  /** Show the document that contains the statement, scroll to the statement and focus it. */
  function showClaim(itemId: string) {
    const located = claims.find(({ claim }) => claim.item_id === itemId)
    if (!located) return
    // Commit the tab switch before focusing: an element in a hidden panel cannot take focus.
    flushSync(() => {
      setActiveDocument(located.documentKind)
      setCoverageTabOpen(false)
    })
    const element = document.getElementById(claimElementId(itemId))
    element?.scrollIntoView({ block: 'center' })
    element?.focus({ preventScroll: true })
  }

  /**
   * Go to a statement. If a dialog or sheet is open it is closed first and
   * the statement is focused only once it has gone (see handleOverlayClosed);
   * focusing earlier would put focus behind a modal that is still on screen.
   */
  function revealClaim(itemId: string) {
    const overlayOpen = printGateOpen || (!twoPane && evidenceSheetOpen)
    if (overlayOpen) {
      claimToReveal.current = itemId
      setPrintGateOpen(false)
      setEvidenceSheetOpen(false)
    } else {
      showClaim(itemId)
    }
  }

  /**
   * Radix calls this when a dialog or sheet has finished closing. Both are
   * opened from code, not by a Radix trigger, so Radix cannot know where
   * focus belongs: it goes to the statement the user asked for, or back to
   * the control that opened the overlay.
   */
  function handleOverlayClosed(event: Event) {
    event.preventDefault()
    const itemId = claimToReveal.current
    claimToReveal.current = null
    if (itemId !== null) showClaim(itemId)
    else overlayOpener.current?.focus()
  }

  async function copyActiveDocument() {
    try {
      await navigator.clipboard.writeText(documentPlainText(generation, activeDocument))
      toast.success(`${DOCUMENT_LABEL[activeDocument]} copied as plain text`)
    } catch {
      toast.error('Copying failed. Select the text in the document and copy it instead.')
    }
  }

  /** Print straight away only when nothing is waiting for the user's review. */
  function requestPrint() {
    if (needsRevalidation || flagged.length > 0) {
      rememberOverlayOpener()
      setPrintGateOpen(true)
    } else {
      window.print()
    }
  }

  function printAnyway() {
    // Close the dialog before the browser takes its snapshot. The print
    // stylesheet also hides any dialog, in case it is still animating out.
    flushSync(() => setPrintGateOpen(false))
    window.print()
  }

  const target = jobLabel(generation)
  const generatedAt = formatDateTime(generation.created_at)

  return (
    <WorkspaceContext
      value={{ generation, evidenceNumbers, selectedEvidenceId, selectEvidence, revealClaim, isWriting }}
    >
      <div className="ws-root space-y-6">
        <div className="print:hidden">
          <PageHeader
            title="Your tailored draft"
            description={
              <>
                <p>
                  {target ? `Tailored for ${target}. ` : null}
                  Every statement shows the evidence it was written from. Select a numbered badge
                  to read the source text.
                </p>
                <p className="mt-1 text-sm">
                  Generated{' '}
                  {generatedAt ? <time dateTime={generation.created_at}>{generatedAt}</time> : null}{' '}
                  with {generation.model}.
                </p>
              </>
            }
          />
        </div>

        {generation.stale ? <StaleBanner reasons={generation.stale_reasons} /> : null}

        <div
          className={cn(
            'ws-layout',
            twoPane && 'grid grid-cols-[minmax(0,1fr)_23rem] items-start gap-8',
          )}
        >
          <Tabs value={mainTab} onValueChange={handleMainTabChange} className="ws-tabs min-w-0 gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2 print:hidden">
              <TabsList className={twoPane ? undefined : 'w-full sm:w-fit'}>
                <TabsTrigger value="resume">{DOCUMENT_LABEL.resume}</TabsTrigger>
                <TabsTrigger value="cover_letter">{DOCUMENT_LABEL.cover_letter}</TabsTrigger>
                {twoPane ? null : <TabsTrigger value={COVERAGE_TAB}>Coverage</TabsTrigger>}
              </TabsList>
              {showingCoverageTab ? null : (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-describedby="document-actions-hint"
                    onClick={() => void copyActiveDocument()}
                  >
                    <Copy aria-hidden="true" />
                    Copy
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    aria-describedby="document-actions-hint"
                    onClick={requestPrint}
                  >
                    <Printer aria-hidden="true" />
                    Print / Save as PDF
                  </Button>
                  <span id="document-actions-hint" className="sr-only">
                    Applies to the {DOCUMENT_LABEL[activeDocument].toLowerCase()} only, without
                    review markers.
                  </span>
                </div>
              )}
            </div>

            {showingCoverageTab ? null : <ValidationBar flagged={flagged} />}

            {DOCUMENT_KINDS.map((kind) => (
              <TabsContent
                key={kind}
                value={kind}
                forceMount
                className="ws-document-panel"
                data-print-target={kind === activeDocument}
                style={hiddenUnless(kind === mainTab)}
              >
                {kind === 'resume' ? (
                  <ResumeDocument resume={generation.resume} />
                ) : (
                  <CoverLetterDocument
                    coverLetter={generation.cover_letter}
                    contact={generation.resume?.contact ?? null}
                  />
                )}
              </TabsContent>
            ))}

            {twoPane ? null : (
              <TabsContent
                value={COVERAGE_TAB}
                forceMount
                className="print:hidden"
                style={hiddenUnless(showingCoverageTab)}
              >
                <CoveragePanel />
              </TabsContent>
            )}
          </Tabs>

          {twoPane ? (
            <aside
              aria-label="Evidence and coverage"
              className="sticky top-20 flex max-h-[calc(100svh-6.5rem)] flex-col rounded-xl border bg-card print:hidden"
            >
              <Tabs value={sideTab} onValueChange={handleSideTabChange} className="min-h-0 flex-1 gap-0">
                <TabsList className="m-3 w-auto self-stretch">
                  <TabsTrigger value="evidence">Evidence</TabsTrigger>
                  <TabsTrigger value="coverage">Coverage</TabsTrigger>
                </TabsList>
                {/* Announced politely, so choosing a badge reads the evidence without moving focus. */}
                <TabsContent
                  value="evidence"
                  aria-live="polite"
                  className="min-h-0 overflow-y-auto px-4 pb-4"
                >
                  <EvidencePanel />
                </TabsContent>
                <TabsContent
                  value="coverage"
                  forceMount
                  className="min-h-0 overflow-y-auto px-4 pb-4"
                  style={hiddenUnless(sideTab === 'coverage')}
                >
                  <CoveragePanel />
                </TabsContent>
              </Tabs>
            </aside>
          ) : (
            <Sheet open={evidenceSheetOpen} onOpenChange={setEvidenceSheetOpen}>
              <SheetContent
                side="bottom"
                className="max-h-[85svh] gap-0 overflow-y-auto"
                onCloseAutoFocus={handleOverlayClosed}
              >
                <SheetHeader>
                  <SheetTitle>
                    {selectedEvidenceId
                      ? evidenceTitle(evidenceNumbers, selectedEvidenceId)
                      : 'Evidence'}
                  </SheetTitle>
                  <SheetDescription>
                    The text from your profile that the statement or requirement cites.
                  </SheetDescription>
                </SheetHeader>
                <div className="px-4 pb-6">
                  {selectedEvidenceId ? <EvidenceDetail evidenceId={selectedEvidenceId} /> : null}
                </div>
              </SheetContent>
            </Sheet>
          )}
        </div>

        <PrintGateDialog
          open={printGateOpen}
          onOpenChange={setPrintGateOpen}
          onCloseAutoFocus={handleOverlayClosed}
          documentKind={activeDocument}
          flagged={flagged}
          needsRevalidation={needsRevalidation}
          onReview={revealClaim}
          onPrint={printAnyway}
        />
      </div>
    </WorkspaceContext>
  )
}
