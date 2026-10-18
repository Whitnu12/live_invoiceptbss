# Reference patch for document_engine.py.
# The current engine already lists TKDN/Opname/package as route/package modes;
# the missing behavior is that render_document_package() only accepts generator
# documents. Keep TKDN/Opname as saved-document adapters and let the new package
# route call document_package_renderer.render_document_package().
PACKAGE_ORDER=['proforma-invoice','rincian-pekerjaan','bamp','basp','bastb','wcc','tkdn','opname']
