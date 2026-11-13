#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <vulkan/vulkan.h>
#include <nvsdk_ngx_vk.h>
#include <string>
static HMODULE runtime=nullptr;
using Init=NVSDK_NGX_Result(*)(unsigned long long,const wchar_t*,VkInstance,VkPhysicalDevice,VkDevice,NVSDK_NGX_Version,const void*);
using Create=NVSDK_NGX_Result(*)(VkCommandBuffer,int,NVSDK_NGX_Parameter*,NVSDK_NGX_Handle**);
using Eval=NVSDK_NGX_Result(*)(VkCommandBuffer,const NVSDK_NGX_Handle*,const NVSDK_NGX_Parameter*,void*);
using Release=NVSDK_NGX_Result(*)(NVSDK_NGX_Handle*);
static Create createFn; static Eval evalFn; static Release releaseFn;
extern "C" __declspec(dllexport) unsigned nr_init(const wchar_t* path,VkInstance instance,VkPhysicalDevice pd,VkDevice device){
 if(runtime)return 1;
 runtime=LoadLibraryExW(path,nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
 if(!runtime)return 0xbad00004;
 auto init=reinterpret_cast<Init>(GetProcAddress(runtime,"NVSDK_NGX_VULKAN_Init_Ext"));
 createFn=reinterpret_cast<Create>(GetProcAddress(runtime,"NVSDK_NGX_VULKAN_CreateFeature"));
 evalFn=reinterpret_cast<Eval>(GetProcAddress(runtime,"NVSDK_NGX_VULKAN_EvaluateFeature"));
 releaseFn=reinterpret_cast<Release>(GetProcAddress(runtime,"NVSDK_NGX_VULKAN_ReleaseFeature"));
 if(!init||!createFn||!evalFn||!releaseFn){FreeLibrary(runtime);runtime=nullptr;return 0xbad00004;}
 std::wstring dir(path);dir.resize(dir.find_last_of(L"/\\"));
 unsigned result=static_cast<unsigned>(init(0x0876232cULL,dir.c_str(),instance,pd,device,NVSDK_NGX_Version_API,nullptr));
 if(result!=1){FreeLibrary(runtime);runtime=nullptr;}
 return result;
}
extern "C" __declspec(dllexport) unsigned nr_create(VkCommandBuffer cmd,int feature,NVSDK_NGX_Parameter* p,NVSDK_NGX_Handle** h){return createFn?static_cast<unsigned>(createFn(cmd,feature,p,h)):0xbad00007;}
extern "C" __declspec(dllexport) unsigned nr_evaluate(VkCommandBuffer cmd,const NVSDK_NGX_Handle* h,const NVSDK_NGX_Parameter* p,void* cb){return evalFn?static_cast<unsigned>(evalFn(cmd,h,p,cb)):0xbad00007;}
extern "C" __declspec(dllexport) unsigned nr_release(NVSDK_NGX_Handle* h){return releaseFn?static_cast<unsigned>(releaseFn(h)):0xbad00007;}
