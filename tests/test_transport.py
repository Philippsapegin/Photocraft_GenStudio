import base64,threading,unittest
from urllib.error import HTTPError
from unittest.mock import Mock,patch
from PIL import Image
from genfill.image_ops import png_bytes
from genstudio import direct_client
from genstudio.transport import poll
from genfill.comfy import Cancelled

class TransportTests(unittest.TestCase):
    def test_busy_worker_timeouts_retry_the_same_read_without_resubmission(self):
        get=Mock(side_effect=[TimeoutError(),ConnectionResetError(),{"state":"done"}])
        result=poll("/jobs/same",request=get,cancelled=lambda:False,pause=lambda _:None)
        self.assertEqual(result,{"state":"done"})
        self.assertEqual([c.args[0] for c in get.call_args_list],["/jobs/same"]*3)
    def test_cancel_and_http_errors_are_not_retried(self):
        cancel=threading.Event();get=Mock(side_effect=TimeoutError())
        with self.assertRaises(Cancelled):poll("/jobs/x",request=get,cancelled=cancel.is_set,pause=lambda _:cancel.set())
        self.assertEqual(get.call_count,1)
        get=Mock(side_effect=HTTPError("/jobs/x",404,"missing",{},None))
        with self.assertRaises(HTTPError):poll("/jobs/x",request=get,cancelled=lambda:False,pause=lambda _:None)
        self.assertEqual(get.call_count,1)
    def test_unreachable_worker_is_bounded(self):
        with self.assertRaises(TimeoutError):poll("/jobs/x",request=Mock(side_effect=TimeoutError()),cancelled=lambda:False,pause=lambda _:None,max_stall=0)
    def test_direct_result_survives_a_worker_poll_timeout(self):
        image=base64.b64encode(png_bytes(Image.new("RGBA",(8,8),(10,20,30,255)))).decode()
        request=Mock(side_effect=[TimeoutError(),{"state":"done","message":"Done"},{"image":image}])
        with patch.object(direct_client,"http_json",request):
            result=direct_client.wait({"id":"same"},threading.Event(),lambda _:None)
        self.assertEqual(result.getpixel((0,0)),(10,20,30,255))
        self.assertEqual([c.args[0].rsplit("/",1)[-1] for c in request.call_args_list],["same","same","result"])
        self.assertTrue(all(len(c.args)==1 for c in request.call_args_list),"polling never submits a second job")
