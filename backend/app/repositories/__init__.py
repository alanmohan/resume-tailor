"""Repositories: the only code that talks to MongoDB collections.

``Repositories`` bundles one repository per collection. Request handlers get
it through the ``ReposDep`` dependency (app/api/deps.py).
"""

from app.db import Database
from app.repositories.counters import CounterRepository
from app.repositories.evidence import EvidenceRepository
from app.repositories.generations import GenerationRepository
from app.repositories.jobs import JobRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.sessions import SessionRepository
from app.repositories.sources import SourceRepository
from app.schemas.sessions import DeletedCounts


class Repositories:
    def __init__(self, db: Database) -> None:
        self.sessions = SessionRepository(db)
        self.sources = SourceRepository(db)
        self.profiles = ProfileRepository(db)
        self.evidence = EvidenceRepository(db)
        self.jobs = JobRepository(db)
        self.generations = GenerationRepository(db)
        self.counters = CounterRepository(db)

    async def delete_all_for_owner(self, owner_id: str) -> DeletedCounts:
        """Delete the owner's documents from every owned collection.

        Derived data goes first (drafts, then evidence) so a reader never finds
        a draft or evidence whose profile is already gone.
        """
        generations = await self.generations.delete_for_owner(owner_id)
        jobs = await self.jobs.delete_for_owner(owner_id)
        evidence = await self.evidence.delete_for_owner(owner_id)
        profiles = await self.profiles.delete_for_owner(owner_id)
        sources = await self.sources.delete_for_owner(owner_id)
        return DeletedCounts(
            sources=sources,
            profiles=profiles,
            evidence=evidence,
            jobs=jobs,
            generations=generations,
        )
