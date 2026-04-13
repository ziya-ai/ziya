"""
Tests for external path persistence across server restarts.

Guards against regression where external paths added to a project
are lost when the server restarts because they were only held in
memory (_explicit_external_paths / _folder_cache).
"""

import os
import sys
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestExternalPathPersistence(unittest.TestCase):
    """External paths must survive server restart via project settings."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ziya_home = Path(self.tmpdir) / ".ziya"
        self.ziya_home.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_project(self, project_path: str) -> 'Project':
        from app.storage.projects import ProjectStorage
        from app.models.project import ProjectCreate
        ps = ProjectStorage(self.ziya_home)
        return ps.create(ProjectCreate(path=project_path, name="test"))

    def test_external_paths_field_exists_on_project_settings(self):
        """ProjectSettings must have an externalPaths field."""
        from app.models.project import ProjectSettings
        settings = ProjectSettings()
        self.assertEqual(settings.externalPaths, [])

    def test_external_paths_round_trip(self):
        """External paths stored in project settings survive a read-back."""
        from app.storage.projects import ProjectStorage
        from app.models.project import ProjectUpdate, ProjectSettings

        project_dir = os.path.join(self.tmpdir, "myproject")
        os.makedirs(project_dir)
        project = self._make_project(project_dir)

        ps = ProjectStorage(self.ziya_home)

        # Simulate what /api/add-explicit-paths does
        ext_paths = ["/Users/dcohn/work/qos", "/Users/dcohn/docs"]
        ps.update(project.id, ProjectUpdate(
            settings=ProjectSettings(externalPaths=ext_paths)
        ))

        # Simulate server restart: new ProjectStorage instance
        ps2 = ProjectStorage(self.ziya_home)
        reloaded = ps2.get(project.id)

        self.assertIsNotNone(reloaded)
        self.assertEqual(sorted(reloaded.settings.externalPaths),
                         sorted(ext_paths))

    def test_external_paths_default_empty(self):
        """Projects created before this feature should have empty externalPaths."""
        from app.storage.projects import ProjectStorage

        project_dir = os.path.join(self.tmpdir, "oldproject")
        os.makedirs(project_dir)
        project = self._make_project(project_dir)

        ps = ProjectStorage(self.ziya_home)
        reloaded = ps.get(project.id)

        self.assertEqual(reloaded.settings.externalPaths, [])

    def test_restore_populates_memory_caches(self):
        """_restore_external_paths_for_project should populate _explicit_external_paths."""
        from app.storage.projects import ProjectStorage
        from app.models.project import ProjectUpdate, ProjectSettings
        import app.server as srv
        import app.services.folder_service as folder_svc

        project_dir = os.path.join(self.tmpdir, "restoretest")
        os.makedirs(project_dir)
        ext_dir = os.path.join(self.tmpdir, "external_data")
        os.makedirs(ext_dir)

        project = self._make_project(project_dir)
        ps = ProjectStorage(self.ziya_home)
        ps.update(project.id, ProjectUpdate(
            settings=ProjectSettings(externalPaths=[ext_dir])
        ))

        # Clear server-side state and restored tracker
        srv._explicit_external_paths.clear()
        folder_svc._restored_projects.discard(os.path.abspath(project_dir))

        # Mock get_ziya_home at the import location used inside
        # _restore_external_paths_for_project (app.utils.paths)
        with unittest.mock.patch('app.utils.paths.get_ziya_home', return_value=self.ziya_home), \
             unittest.mock.patch('app.server.add_external_path_to_cache', return_value=True):
            srv._restore_external_paths_for_project(project_dir)

        self.assertIn(ext_dir, srv._explicit_external_paths)


if __name__ == '__main__':
    unittest.main()
