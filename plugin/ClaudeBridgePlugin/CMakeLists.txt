start_sources()
sources_platform(ALL)
add_sources("NoUberFile"
	SOURCE_GROUP "Root"
		"ClaudeBridgePlugin.cpp"
		"ClaudeBridgePlugin.h"
		"TcpCommandServer.cpp"
		"TcpCommandServer.h"
		"StdAfx.h"
		"StdAfx.cpp"
)
end_sources()

CryEditorPlugin(ClaudeBridgePlugin PCH "StdAfx.cpp" SOLUTION_FOLDER "Sandbox/Plugins")

# CHeightmap is already reachable via the abstract IEditor interface
# (GetIEditor()->GetHeightmap()), but CTerrainManager/CLayer (needed to set a
# custom terrain layer texture) are EditorQt-internal with no such
# interface -- only reachable via CEditorImpl::GetTerrainManager(). Linking
# directly against the "Sandbox" target (EditorQt/CMakeLists.txt's own
# CryEditor(Sandbox ...) target -- Sandbox.exe, built within this same CMake
# project) pulls in both its import library (Sandbox.lib -- MSVC generates
# one for any target with dllexport symbols, EXE or DLL) and its PUBLIC
# include directories (EditorQt's own root, so <IEditorImpl.h>,
# <Terrain/Heightmap.h> etc resolve), the same way linking against
# EditorCommon already does above via CryEditorPlugin().
target_link_libraries(ClaudeBridgePlugin PRIVATE Sandbox)
