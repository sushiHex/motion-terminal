"""Import-scale signature smoke: confirm the 5.8 import-scale API surface.
Run headless via -run=pythonscript; the printed values are the record."""
import unreal

p = unreal.InterchangeGenericAssetsPipeline()
print('IGAP.import_offset_uniform_scale =', p.get_editor_property('import_offset_uniform_scale'))
print('IGAP.import_offset_translation  =', p.get_editor_property('import_offset_translation'))
print('IGAP.import_offset_rotation     =', p.get_editor_property('import_offset_rotation'))
print('IGAP scale-ish props:', [n for n in dir(unreal.InterchangeGenericAssetsPipeline) if 'scale' in n.lower()])

d = unreal.FbxSkeletalMeshImportData()
print('FbxSkelImport.import_uniform_scale =', d.get_editor_property('import_uniform_scale'))
print('FbxSkelImport scale-ish props:', [n for n in dir(unreal.FbxSkeletalMeshImportData) if 'scale' in n.lower()])

print('AssetImportTask exists:', hasattr(unreal, 'AssetImportTask'))
print('SIGNATURE-SMOKE-OK')
