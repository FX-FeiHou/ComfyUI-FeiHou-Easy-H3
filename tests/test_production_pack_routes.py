import zipfile
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
import test_production_pack as fixture_module
pack = fixture_module.pack


class RouteTests(AioHTTPTestCase):
    async def get_application(self):
        self.fixture = fixture_module.PackTests()
        self.fixture.setUp()
        routes = web.RouteTableDef()
        pack.register_routes(routes, lambda request: request.remote == "127.0.0.1")
        app = web.Application()
        app.add_routes(routes)
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.fixture.tearDown()

    async def test_inspect_prepare_preview_and_request_guard(self):
        base = "/feihou_easy_h3/production_pack/"
        source = str(self.fixture.package)
        denied = await self.client.post(base + "inspect", json={"source": source})
        self.assertEqual(denied.status, 403)
        headers = {"X-FeiHou-Pack": "1"}
        denied = await self.client.post(base + "inspect", headers={**headers, "Origin": "https://untrusted.invalid"}, json={"source": source})
        self.assertEqual(denied.status, 403)
        response = await self.client.post(base + "inspect", headers=headers, json={"source": source})
        self.assertEqual(response.status, 200)
        info = await response.json()
        response = await self.client.post(base + "prepare", headers=headers, json={"package_id": info["package_id"], "shots": [self.fixture.shot]})
        self.assertEqual(response.status, 200)
        ready = await response.json()
        response = await self.client.post(base + "preview", headers=headers, json={"source": source, "package_id": ready["package_id"], "shot_index": 1, "prompt_language": "zh"})
        shot = await response.json()
        self.assertEqual(shot["params"]["seconds"], 4.8)
        self.assertEqual(len(shot["media"]), 3)

    async def test_raw_zip_upload(self):
        path = self.fixture.root / "upload.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for f in self.fixture.package.iterdir():
                archive.write(f, f.name)
        with path.open("rb") as archive:
            response = await self.client.post("/feihou_easy_h3/production_pack/upload", headers={"X-FeiHou-Pack": "1"}, data=archive)
        self.assertEqual(response.status, 200)
        info = await response.json()
        self.assertIn("html", info)
        self.assertTrue(info["source"].endswith(".zip"))
