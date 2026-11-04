/* Read-only WinPE guard. The deployment script owns diskpart; this program never writes disks. */
#define _WIN32_WINNT 0x0601
#include <winsock2.h>
#include <windows.h>
#include <iphlpapi.h>
#include <winioctl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 4) return 2;
    unsigned int expected[6];
    if (strlen(argv[1]) != 17 || sscanf(argv[1], "%2x-%2x-%2x-%2x-%2x-%2x", &expected[0], &expected[1], &expected[2], &expected[3], &expected[4], &expected[5]) != 6) return 2;
    ULONG size=16384, result; IP_ADAPTER_ADDRESSES *adapters=NULL;
    for (int attempt=0; attempt<3; attempt++) {
        free(adapters); adapters=malloc(size); if (!adapters) return 3;
        result=GetAdaptersAddresses(AF_UNSPEC, GAA_FLAG_SKIP_ANYCAST|GAA_FLAG_SKIP_MULTICAST|GAA_FLAG_SKIP_DNS_SERVER, NULL, adapters, &size);
        if (result != ERROR_BUFFER_OVERFLOW) break;
    }
    int found=0;
    if (result==NO_ERROR) for (IP_ADAPTER_ADDRESSES *a=adapters; a; a=a->Next) {
        if (a->PhysicalAddressLength!=6 || a->OperStatus!=IfOperStatusUp) continue;
        int same=1; for(int i=0;i<6;i++) if(a->PhysicalAddress[i]!=(BYTE)expected[i]) same=0;
        if(same) found=1;
    }
    free(adapters);
    if(!found) { fputs("Target MAC is not an active adapter. No disk changes made.\n",stderr); return 4; }
    HANDLE disk=CreateFileA("\\\\.\\PhysicalDrive0",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(disk==INVALID_HANDLE_VALUE) { fputs("Cannot inspect disk 0.\n",stderr); return 5; }
    BYTE buffer[65536]; DWORD count;
    STORAGE_PROPERTY_QUERY query={StorageDeviceProperty,PropertyStandardQuery,{0}};
    if(!DeviceIoControl(disk,IOCTL_STORAGE_QUERY_PROPERTY,&query,sizeof(query),buffer,sizeof(buffer),&count,NULL)) { CloseHandle(disk); return 6; }
    STORAGE_DEVICE_DESCRIPTOR *desc=(STORAGE_DEVICE_DESCRIPTOR*)buffer;
    if(desc->RemovableMedia || desc->BusType==BusTypeUsb) { fputs("Disk 0 is removable/USB. Refusing to erase it.\n",stderr); CloseHandle(disk); return 7; }
    GET_LENGTH_INFORMATION length;
    if(!DeviceIoControl(disk,IOCTL_DISK_GET_LENGTH_INFO,NULL,0,&length,sizeof(length),&count,NULL) || (unsigned long long)length.Length.QuadPart<strtoull(argv[2],NULL,10)) { fputs("Disk 0 is too small or unreadable.\n",stderr); CloseHandle(disk); return 8; }
    if(strcmp(argv[3],"1")==0) {
        if(!DeviceIoControl(disk,IOCTL_DISK_GET_DRIVE_LAYOUT_EX,NULL,0,buffer,sizeof(buffer),&count,NULL)) { CloseHandle(disk); return 9; }
        DRIVE_LAYOUT_INFORMATION_EX *layout=(DRIVE_LAYOUT_INFORMATION_EX*)buffer;
        if(layout->PartitionCount) { fputs("Existing partitions require explicit wipe authorization.\n",stderr); CloseHandle(disk); return 10; }
    }
    CloseHandle(disk); puts("Target adapter and internal disk 0 verified."); return 0;
}
