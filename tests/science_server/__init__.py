"""Tests for the ``/v1/science/*`` server seam (spec §17.1, ENG-002).

The router in ``omnigent/server/routes/science.py`` is a thin adapter
over ``omnisci.service.ScienceService``; these tests pin the wire
contract from ``science/docs/server-api-contract.md`` — endpoint paths,
the ``?project=`` addressing scheme, response shapes, and the error
envelope (404 not-found, 409 state conflict, 400 validation, 503 when
the optional omnisci package is unavailable). The web UI client
(``web/src/lib/scienceApi.ts``) implements against the same contract.
"""
